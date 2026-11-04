"""Utilidades compartidas de la API REST /api/v1.

La API es independiente de la autenticación por cookie del portal HTML: usa
JWT (HS256) firmados con ``SECRET_KEY`` y guarda la vigencia de los refresh
tokens en ``notificaciones`` (no hay una tabla específica de sesiones).

Todas las lecturas son tolerantes a fallos (si Supabase no responde se usan
listas vacías o valores por defecto). Las escrituras sí propagan el error para
que el cliente reciba un 400/409/500 con un mensaje claro.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from typing import Any, Iterable

from flask import current_app, g, jsonify, request

from app.config import Config
from app.exceptions import (
    AppError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.services import agenda_service, inventario_service
from app.supabase_client import error_postgrest, get_admin_client, get_reader

log = logging.getLogger(__name__)

ACCESS_TTL = 60 * 60                 # 1 hora
REFRESH_TTL = 30 * 24 * 60 * 60      # 30 días
RESET_TTL = 30 * 60                  # 30 minutos

# Tipos internos de notificaciones: nunca se muestran en la campana del usuario.
TIPOS_INTERNOS = {
    "API_REFRESH",
    "CITA_FOTO",
    "CITA_META",
    "IDEMPOTENCIA",
    "PASSWORD_RESET",
    "PRODUCTO_META",
    "PRODUCTO_PRECIO",
    "STOCK_MOVIMIENTO",
    "STAFF_ESTADO",
    "VAN_STOCK",
    "CONTACTO",
    "OPINION",
}

ROLES_VALIDOS = (
    "ADMINISTRADOR",
    "SUPERVISOR",
    "TECNICO",
    "RECEPCIONISTA",
    "VENDEDOR",
    "ALMACENERO",
    "CLIENTE",
)

# Permisos que devuelve E17. Cada permiso corresponde a una capacidad de la API.
PERMISOS_POR_ROL = {
    "ADMINISTRADOR": ["*"],
    "SUPERVISOR": [
        "citas.ver",
        "citas.asignar",
        "citas.ruta",
        "productos.ver",
        "staff.ver",
        "staff.disponibilidad.ver",
        "staff.van-stock",
        "ordenes.ver",
    ],
    "TECNICO": [
        "staff.ver",
        "staff.citas.ver",
        "staff.citas.actualizar",
        "staff.reparaciones.ver",
        "staff.reparaciones.cerrar",
        "staff.disponibilidad.gestionar",
        "staff.van-stock",
        "notificaciones.ver",
    ],
    "RECEPCIONISTA": [
        "citas.ver",
        "citas.crear",
        "citas.asignar",
        "productos.ver",
        "notificaciones.ver",
    ],
    "VENDEDOR": [
        "ventas.crear",
        "ordenes.ver",
        "productos.ver",
        "notificaciones.ver",
    ],
    "ALMACENERO": [
        "productos.ver",
        "productos.gestionar",
        "inventario.gestionar",
        "staff.van-stock",
        "notificaciones.ver",
    ],
    "CLIENTE": [
        "carrito.gestionar",
        "citas.gestionar",
        "pedidos.ver",
        "perfil.ver",
        "notificaciones.ver",
    ],
}

_CORREO_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------

class ApiError(AppError):
    """Error de la API con código estable y detalle por campo."""

    def __init__(self, message: str, status_code: int = 400, codigo: str = "ERROR", detalles=None):
        payload = {"codigo": codigo}
        if detalles:
            payload["detalles"] = detalles
        super().__init__(message=message, status_code=status_code, payload=payload)
        self.codigo = codigo

    def to_payload(self) -> dict:
        data = {
            "codigo": self.codigo,
            "mensaje": self.message,
            "error": self.message,
            "status_code": self.status_code,
        }
        if self.payload and self.payload.get("detalles"):
            data["detalles"] = self.payload["detalles"]
        return data


def error_response(status: int, codigo: str, mensaje: str, detalles=None):
    data = {
        "codigo": codigo,
        "mensaje": mensaje,
        "error": mensaje,
        "status_code": status,
    }
    if detalles:
        data["detalles"] = detalles
    return jsonify(data), status


def api_route(fn):
    """Captura excepciones y las convierte en respuestas JSON homogéneas."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ApiError as exc:
            return jsonify(exc.to_payload()), exc.status_code
        except ValidationError as exc:
            # El resto del portal usa 422; la especificación de la API pide 400.
            return error_response(400, "DATOS_INVALIDOS", exc.message, exc.payload)
        except NotFoundError as exc:
            return error_response(404, "NO_ENCONTRADO", exc.message, exc.payload)
        except ConflictError as exc:
            return error_response(409, "CONFLICTO", exc.message, exc.payload)
        except AuthorizationError as exc:
            return error_response(403, "SIN_PERMISO", exc.message, exc.payload)
        except AppError as exc:
            return error_response(exc.status_code, "ERROR", exc.message, exc.payload)
        except Exception as exc:  # noqa: BLE001
            log.exception("Error no controlado en API %s %s: %s", request.method, request.path, exc)
            return error_response(
                500,
                "ERROR_INTERNO",
                "Ocurrió un error interno. Vuelve a intentarlo.",
            )

    return wrapper


# ---------------------------------------------------------------------------
# JWT (HS256, sin dependencias externas)
# ---------------------------------------------------------------------------

def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _secreto() -> bytes:
    return (current_app.config.get("SECRET_KEY") or Config.SECRET_KEY or "").encode("utf-8")


def crear_token(usuario: dict, tipo: str = "access", ttl: int = ACCESS_TTL) -> tuple[str, dict]:
    """Crea un JWT y devuelve (token, payload)."""
    ahora = int(time.time())
    payload = {
        "sub": int(usuario.get("id")),
        "usuario": usuario.get("nombre_usuario") or "",
        "rol": (usuario.get("rol") or "").upper(),
        "type": tipo,
        "jti": secrets.token_urlsafe(24),
        "iat": ahora,
        "exp": ahora + int(ttl),
    }
    header = {"alg": "HS256", "typ": "JWT"}
    partes = [
        _b64e(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ]
    firma = hmac.new(_secreto(), ".".join(partes).encode("ascii"), hashlib.sha256).digest()
    return ".".join(partes + [_b64e(firma)]), payload


def decodificar_token(token: str | None, tipo: str | None = None) -> dict | None:
    if not token or token.count(".") != 2:
        return None
    try:
        header_b64, payload_b64, firma_b64 = token.split(".")
        esperada = hmac.new(
            _secreto(), f"{header_b64}.{payload_b64}".encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(esperada, _b64d(firma_b64)):
            return None
        payload = json.loads(_b64d(payload_b64).decode("utf-8"))
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
        if tipo and payload.get("type") != tipo:
            return None
        return payload
    except Exception:  # noqa: BLE001
        return None


def _token_autorizacion() -> str | None:
    cabecera = request.headers.get("Authorization", "")
    if cabecera.lower().startswith("bearer "):
        return cabecera[7:].strip()
    return None


def _registrar_refresh(usuario_id: int, jti: str, exp: int) -> None:
    try:
        insert_row(
            "notificaciones",
            {
                "usuario_id": usuario_id,
                "tipo": "API_REFRESH",
                "titulo": "Sesión API",
                "mensaje": json.dumps({"exp": exp}),
                "referencia": jti,
                "canal": "API",
                "leida": False,
                "fecha_creacion": now_utc_iso(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo registrar el refresh token: %s", exc)


def _refresh_vigente(jti: str, usuario_id: int) -> bool:
    fila = read_one("notificaciones", tipo="API_REFRESH", referencia=jti, usuario_id=usuario_id)
    if not fila:
        return False
    return not bool(fila.get("leida"))


def _revocar_refresh(jti: str) -> None:
    try:
        get_admin_client().table("notificaciones").update(
            {"leida": True, "fecha_lectura": now_utc_iso()}
        ).eq("tipo", "API_REFRESH").eq("referencia", jti).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo revocar el refresh token: %s", exc)


def emitir_sesion(usuario: dict) -> dict:
    access, _ = crear_token(usuario, "access", ACCESS_TTL)
    refresh, payload = crear_token(usuario, "refresh", REFRESH_TTL)
    _registrar_refresh(int(usuario["id"]), payload["jti"], payload["exp"])
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": ACCESS_TTL,
        "token_type": "Bearer",
    }


def _contexto_usuario(usuario: dict) -> dict:
    rol = (usuario.get("rol") or "").upper()
    return {
        "id": int(usuario.get("id")),
        "usuario": usuario,
        "rol": rol,
        "cliente_id": usuario.get("cliente_id"),
        "empleado_id": usuario.get("empleado_id"),
        "permisos": PERMISOS_POR_ROL.get(rol, []),
    }


def api_auth(*roles: str):
    """Exige un access token válido y, opcionalmente, uno de los roles dados."""

    permitidos = {r.upper() for r in roles if r}

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            payload = decodificar_token(_token_autorizacion(), "access")
            if not payload:
                raise ApiError("Token ausente, inválido o vencido.", 401, "NO_AUTENTICADO")
            usuario = read_one("usuarios", id=payload.get("sub"))
            if not usuario:
                raise ApiError("El usuario ya no existe.", 401, "NO_AUTENTICADO")
            if usuario.get("activo") is False:
                raise ApiError("La cuenta está inactiva.", 403, "CUENTA_INACTIVA")
            rol = (usuario.get("rol") or "").upper()
            if permitidos and rol not in permitidos:
                raise ApiError("Tu rol no tiene permiso para esta operación.", 403, "SIN_PERMISO")
            g.api_user = _contexto_usuario(usuario)
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def usuario_actual() -> dict:
    return (getattr(g, "api_user", None) or {}).get("usuario") or {}


def contexto_actual() -> dict:
    return getattr(g, "api_user", None) or {}


def rol_actual() -> str:
    return (usuario_actual().get("rol") or "").upper()


def es_admin() -> bool:
    return rol_actual() == "ADMINISTRADOR"


def es_trabajador() -> bool:
    return rol_actual() in {"SUPERVISOR", "TECNICO", "RECEPCIONISTA", "VENDEDOR", "ALMACENERO"}


def permisos_actuales() -> list:
    return PERMISOS_POR_ROL.get(rol_actual(), [])


def tecnico_actual(crear: bool = False):
    """Devuelve la fila de ``tecnicos`` del usuario autenticado (si es técnico)."""
    u = usuario_actual()
    if not u:
        return None
    empleado_id = u.get("empleado_id")
    if not empleado_id:
        return None
    fila = read_one("tecnicos", empleado_id=empleado_id)
    if fila or not crear or rol_actual() != "TECNICO":
        return fila
    try:
        return insert_row(
            "tecnicos",
            {"empleado_id": empleado_id, "especialidad": "GENERAL", "nivel": "JUNIOR"},
        )
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Lectura del cuerpo / parámetros
# ---------------------------------------------------------------------------

def cuerpo() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    if request.form:
        return request.form.to_dict()
    return {}


def lista_cuerpo(campo: str) -> list:
    data = request.get_json(silent=True) or {}
    valor = data.get(campo)
    return valor if isinstance(valor, list) else []


def texto(valor) -> str:
    return ("" if valor is None else str(valor)).strip()


def entero(valor, defecto: int = 0) -> int:
    try:
        if valor is None or str(valor).strip() == "":
            return defecto
        return int(float(str(valor).strip()))
    except (TypeError, ValueError):
        return defecto


def decimal(valor, defecto: float = 0.0) -> float:
    try:
        if valor is None or str(valor).strip() == "":
            return defecto
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return defecto


def booleano(valor, defecto: bool = False) -> bool:
    if isinstance(valor, bool):
        return valor
    if valor is None or str(valor).strip() == "":
        return defecto
    return str(valor).strip().lower() in {"1", "true", "si", "sí", "yes", "on"}


def arg_entero(nombre: str, defecto: int, minimo: int = 0, maximo: int | None = None) -> int:
    valor = entero(request.args.get(nombre), defecto)
    valor = max(minimo, valor)
    if maximo is not None:
        valor = min(maximo, valor)
    return valor


def arg_booleano(nombre: str, defecto: bool = False) -> bool:
    if nombre not in request.args:
        return defecto
    return booleano(request.args.get(nombre), defecto)


def paginacion(defecto_limit: int = 20, max_limit: int = 50) -> tuple[int, int, int]:
    page = max(1, arg_entero("page", 1, 1))
    limit = arg_entero("limit", defecto_limit, 1, max_limit)
    return page, limit, (page - 1) * limit


def requerir(data: dict, campo: str, etiqueta: str | None = None) -> str:
    valor = texto(data.get(campo))
    if not valor:
        raise ApiError(
            f"El campo {etiqueta or campo} es obligatorio.",
            400,
            "DATOS_INVALIDOS",
            {campo: "obligatorio"},
        )
    return valor


def correo_valido(valor: str) -> bool:
    return bool(_CORREO_RE.match((valor or "").strip().lower()))


def telefono_valido(valor: str, permitir_vacio: bool = True) -> bool:
    valor = texto(valor)
    if not valor:
        return permitir_vacio
    return valor.isdigit() and len(valor) <= 9


def validar_fecha(valor: str, permitir_pasado: bool = False) -> date:
    try:
        fecha = date.fromisoformat(texto(valor))
    except (TypeError, ValueError):
        raise ApiError("La fecha no es válida (formato YYYY-MM-DD).", 400, "DATOS_INVALIDOS")
    if not permitir_pasado and fecha < hoy_lima():
        raise ApiError("La fecha no puede estar en el pasado.", 400, "DATOS_INVALIDOS")
    return fecha


def validar_hora(valor: str) -> str:
    valor = texto(valor)[:5]
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", valor):
        raise ApiError("La hora no es válida (formato HH:MM).", 400, "DATOS_INVALIDOS")
    return valor


def sumar_hora(hora: str, horas: int = 1) -> str:
    try:
        h, m = map(int, texto(hora).split(":"))
        return f"{(h + horas) % 24:02d}:{m:02d}"
    except (TypeError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# Fechas / formatos
# ---------------------------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def hoy_lima() -> date:
    return agenda_service.hoy_lima()


def ahora_lima() -> datetime:
    return agenda_service.ahora_lima()


def fecha_iso(valor) -> str | None:
    """Normaliza un date/timestamp de PostgREST a YYYY-MM-DD."""
    if not valor:
        return None
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor.isoformat()
    try:
        d = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(valor)[:10]
    return d.date().isoformat()


def datetime_iso(valor) -> str | None:
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor.isoformat()
    return str(valor)


def hhmm(valor) -> str:
    if valor is None:
        return ""
    if hasattr(valor, "strftime"):
        return valor.strftime("%H:%M")
    return str(valor)[:5]


def dinero(valor) -> float:
    try:
        return round(float(valor or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def numero_o_none(valor):
    if valor is None or valor == "":
        return None
    try:
        return round(float(valor), 2)
    except (TypeError, ValueError):
        return None


def fecha_mas_dias(valor, dias: int) -> str | None:
    base = fecha_iso(valor)
    if not base:
        return None
    try:
        return (date.fromisoformat(base) + timedelta(days=dias)).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Acceso a Supabase
# ---------------------------------------------------------------------------

def read_rows(
    tabla: str,
    columnas: str = "*",
    *,
    order: str | None = None,
    desc: bool = False,
    limit: int | None = None,
    in_filtros: dict | None = None,
    **filtros,
) -> list[dict]:
    try:
        consulta = get_reader().table(tabla).select(columnas)
        for campo, valor in filtros.items():
            consulta = consulta.eq(campo, valor)
        for campo, valores in (in_filtros or {}).items():
            consulta = consulta.in_(campo, list(valores or []))
        if order:
            consulta = consulta.order(order, desc=desc)
        if limit:
            consulta = consulta.limit(limit)
        return consulta.execute().data or []
    except Exception as exc:  # noqa: BLE001
        log.warning("Lectura Supabase %s falló: %s", tabla, exc)
        return []


def read_one(tabla: str, columnas: str = "*", **filtros) -> dict | None:
    filas = read_rows(tabla, columnas, limit=1, **filtros)
    return filas[0] if filas else None


def insert_row(tabla: str, datos: dict) -> dict:
    try:
        resp = get_admin_client().table(tabla).insert(datos).execute()
        return (resp.data or [{}])[0]
    except Exception as exc:  # noqa: BLE001
        raise ApiError(
            f"No se pudo guardar en {tabla}: {error_postgrest(exc)}",
            500,
            "ERROR_ESCRITURA",
        ) from exc


def update_row(tabla: str, datos: dict, **filtros) -> dict:
    try:
        consulta = get_admin_client().table(tabla).update(datos)
        for campo, valor in filtros.items():
            consulta = consulta.eq(campo, valor)
        resp = consulta.execute()
        return (resp.data or [{}])[0]
    except Exception as exc:  # noqa: BLE001
        raise ApiError(
            f"No se pudo actualizar {tabla}: {error_postgrest(exc)}",
            500,
            "ERROR_ESCRITURA",
        ) from exc


def delete_row(tabla: str, **filtros) -> None:
    try:
        consulta = get_admin_client().table(tabla).delete()
        for campo, valor in filtros.items():
            consulta = consulta.eq(campo, valor)
        consulta.execute()
    except Exception as exc:  # noqa: BLE001
        raise ApiError(
            f"No se pudo eliminar de {tabla}: {error_postgrest(exc)}",
            500,
            "ERROR_ESCRITURA",
        ) from exc


def contar(tabla: str, **filtros) -> int:
    try:
        consulta = get_reader().table(tabla).select("id", count="exact").limit(1)
        for campo, valor in filtros.items():
            consulta = consulta.eq(campo, valor)
        return int(consulta.execute().count or 0)
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# Metadatos en notificaciones
# ---------------------------------------------------------------------------

def meta_guardar(
    tipo: str,
    referencia,
    datos: Any,
    *,
    usuario_id: int | None = None,
    cliente_id: int | None = None,
    titulo: str | None = None,
) -> dict | None:
    fila = {
        "tipo": tipo,
        "referencia": str(referencia),
        "titulo": titulo or tipo,
        "mensaje": json.dumps(datos, ensure_ascii=False),
        "canal": "API",
        "leida": False,
        "fecha_creacion": now_utc_iso(),
    }
    if usuario_id:
        fila["usuario_id"] = usuario_id
    if cliente_id:
        fila["cliente_id"] = cliente_id
    try:
        return insert_row("notificaciones", fila)
    except ApiError:
        log.warning("No se pudo guardar metadato %s/%s", tipo, referencia)
        return None


def meta_obtener(tipo: str, referencia) -> Any:
    fila = read_rows(
        "notificaciones",
        "id,mensaje,fecha_creacion",
        tipo=tipo,
        referencia=str(referencia),
        order="id",
        desc=True,
        limit=1,
    )
    if not fila:
        return None
    try:
        return json.loads(fila[0].get("mensaje") or "null")
    except (TypeError, ValueError):
        return None


def notificar(
    *,
    usuario_id: int | None = None,
    cliente_id: int | None = None,
    tipo: str,
    titulo: str,
    mensaje: str,
    referencia: str | None = None,
) -> dict | None:
    fila = {
        "usuario_id": usuario_id,
        "cliente_id": cliente_id,
        "tipo": tipo,
        "titulo": titulo,
        "mensaje": mensaje,
        "referencia": referencia,
        "canal": "SISTEMA",
        "leida": False,
        "fecha_creacion": now_utc_iso(),
    }
    try:
        return insert_row("notificaciones", fila)
    except ApiError:
        return None


# ---------------------------------------------------------------------------
# Stock y productos
# ---------------------------------------------------------------------------

def stock_info(producto_ids: Iterable) -> dict:
    ids = [pid for pid in dict.fromkeys(producto_ids or []) if pid is not None]
    if not ids:
        return {}
    datos = inventario_service.stock_por_producto(ids)
    actual = datos.get("stock", {})
    reservado = datos.get("reservado", {})
    salida = {}
    for pid in ids:
        a = float(actual.get(pid, 0) or 0)
        r = float(reservado.get(pid, 0) or 0)
        salida[pid] = {
            "actual": a,
            "reservado": r,
            "disponible": max(0.0, a - r),
        }
    return salida


def etiqueta_stock(stock: float) -> str:
    return "Bajo stock" if float(stock or 0) <= 5 else "En stock"


SELECT_PRODUCTO = (
    "id,codigo,nombre,descripcion,modelo,tipo_producto,imagen_url,fecha_creacion,"
    "fecha_actualizacion,marca_id,categoria_id,precio_venta,precio_promocional,"
    "precio_compra,activo,marcas(id,nombre),categorias(id,nombre)"
)

SELECT_PRODUCTO_SIN_IMG = (
    "id,codigo,nombre,descripcion,modelo,tipo_producto,fecha_creacion,"
    "fecha_actualizacion,marca_id,categoria_id,precio_venta,precio_promocional,"
    "precio_compra,activo,marcas(id,nombre),categorias(id,nombre)"
)


def productos_catalogo(activos: bool | None = True) -> list[dict]:
    for columnas in (SELECT_PRODUCTO, SELECT_PRODUCTO_SIN_IMG):
        try:
            consulta = get_reader().table("productos").select(columnas)
            if activos is True:
                consulta = consulta.eq("activo", True)
            elif activos is False:
                consulta = consulta.eq("activo", False)
            return consulta.order("id").execute().data or []
        except Exception:  # noqa: BLE001
            continue
    return []


def producto_por_id(producto_id: int) -> dict | None:
    for columnas in (SELECT_PRODUCTO, SELECT_PRODUCTO_SIN_IMG):
        try:
            resp = (
                get_reader()
                .table("productos")
                .select(columnas)
                .eq("id", producto_id)
                .limit(1)
                .execute()
            )
            if resp.data:
                return resp.data[0]
        except Exception:  # noqa: BLE001
            continue
    return None


def producto_publico(p: dict, stock: float | None = None) -> dict:
    marca = p.get("marcas") or {}
    categoria = p.get("categorias") or {}
    precio = dinero(p.get("precio_venta"))
    oferta = numero_o_none(p.get("precio_promocional"))
    en_oferta = bool(oferta is not None and oferta < precio)
    if stock is None:
        stock = stock_info([p.get("id")]).get(p.get("id"), {}).get("disponible", 0)
    return {
        "id": p.get("id"),
        "sku": p.get("codigo") or f"PRO-{p.get('id')}",
        "nombre": p.get("nombre"),
        "marca": {"id": marca.get("id") or p.get("marca_id"), "nombre": marca.get("nombre") or ""},
        "categoria": {
            "id": categoria.get("id") or p.get("categoria_id"),
            "nombre": categoria.get("nombre") or "",
        },
        "precio": precio,
        "precio_oferta": oferta if en_oferta else None,
        "en_oferta": en_oferta,
        "imagen_url": p.get("imagen_url"),
        "stock_disponible": int(round(float(stock or 0))),
        "etiqueta": etiqueta_stock(stock),
    }


def productos_publicos(filas: list[dict]) -> list[dict]:
    stocks = stock_info([p.get("id") for p in filas])
    return [
        producto_publico(p, stocks.get(p.get("id"), {}).get("disponible", 0))
        for p in filas
    ]


def stock_minimo_producto(producto_id) -> int:
    meta = meta_obtener("PRODUCTO_META", producto_id) or {}
    try:
        return max(0, int(meta.get("stock_minimo", 10)))
    except (TypeError, ValueError):
        return 10


def meta_producto(producto_id) -> dict:
    return meta_obtener("PRODUCTO_META", producto_id) or {}


def compatibilidad_producto(producto_id) -> list[dict]:
    meta = meta_producto(producto_id)
    ids = meta.get("compatibilidad_marca_ids") or []
    if not ids:
        return []
    filas = read_rows("marcas", "id,nombre", in_filtros={"id": ids})
    return [{"id": m.get("id"), "nombre": m.get("nombre")} for m in filas]


def historial_precios(producto_id, precio_actual=None) -> list[dict]:
    filas = read_rows(
        "notificaciones",
        "id,mensaje,fecha_creacion",
        tipo="PRODUCTO_PRECIO",
        referencia=str(producto_id),
        order="id",
        desc=True,
        limit=30,
    )
    salida = []
    for f in filas:
        try:
            datos = json.loads(f.get("mensaje") or "{}")
        except (TypeError, ValueError):
            datos = {}
        salida.append(
            {
                "fecha": fecha_iso(datos.get("fecha") or f.get("fecha_creacion")),
                "precio": dinero(datos.get("precio")),
            }
        )
    if not salida and precio_actual is not None:
        salida.append({"fecha": hoy_lima().isoformat(), "precio": dinero(precio_actual)})
    return salida


def mover_stock(producto_id: int, tipo: str, cantidad: int, motivo: str, usuario_id: int | None = None) -> dict:
    """Entrada/salida/ajuste de stock. Ajuste fija el stock absoluto."""
    tipo = (tipo or "").upper()
    if tipo not in {"ENTRADA", "SALIDA", "AJUSTE"}:
        raise ApiError("El tipo de movimiento debe ser ENTRADA, SALIDA o AJUSTE.", 400, "DATOS_INVALIDOS")
    cantidad = int(cantidad or 0)
    if cantidad <= 0 and tipo != "AJUSTE":
        raise ApiError("La cantidad debe ser mayor que cero.", 400, "DATOS_INVALIDOS")
    filas = read_rows("inventario", "id,stock_actual", producto_id=producto_id, order="id")
    actual = sum(float(f.get("stock_actual") or 0) for f in filas)
    if tipo == "ENTRADA":
        nuevo = actual + cantidad
    elif tipo == "SALIDA":
        nuevo = max(0.0, actual - cantidad)
    else:
        nuevo = float(cantidad)

    try:
        if filas:
            update_row("inventario", {"stock_actual": nuevo, "fecha_actualizacion": now_utc_iso()}, id=filas[0]["id"])
        else:
            insert_row(
                "inventario",
                {
                    "producto_id": producto_id,
                    "stock_actual": nuevo,
                    "stock_reservado": 0,
                    "fecha_actualizacion": now_utc_iso(),
                },
            )
        fila = meta_guardar(
            "STOCK_MOVIMIENTO",
            f"{producto_id}-{int(time.time() * 1000)}",
            {
                "producto_id": producto_id,
                "tipo": tipo,
                "cantidad": cantidad,
                "stock_anterior": actual,
                "stock_actual": nuevo,
                "motivo": motivo,
                "usuario_id": usuario_id,
                "fecha": now_utc_iso(),
            },
            usuario_id=usuario_id,
        )
    except ApiError:
        raise
    return {"movimiento_id": (fila or {}).get("id") or int(time.time()), "stock_actual": int(round(nuevo))}


# ---------------------------------------------------------------------------
# Personas / usuarios
# ---------------------------------------------------------------------------

def nombre_persona(persona: dict | None) -> str:
    if not persona:
        return ""
    razon = texto(persona.get("razon_social"))
    if razon:
        return razon
    return " ".join(
        parte for parte in (texto(persona.get("nombres")), texto(persona.get("apellidos"))) if parte
    ).strip()


def nombre_usuario_completo(usuario: dict | None) -> str:
    u = usuario or {}
    for relacion in ("clientes", "empleados"):
        persona = u.get(relacion)
        if isinstance(persona, list):
            persona = persona[0] if persona else {}
        nombre = nombre_persona(persona)
        if nombre:
            return nombre
    return texto(u.get("nombre_usuario"))


def perfil_persona(usuario: dict) -> tuple[str, dict, str]:
    """Devuelve (tabla, fila, nombre) de la persona vinculada al usuario."""
    if usuario.get("cliente_id"):
        fila = read_one("clientes", id=usuario.get("cliente_id")) or {}
        return "clientes", fila, nombre_persona(fila)
    if usuario.get("empleado_id"):
        fila = read_one("empleados", id=usuario.get("empleado_id")) or {}
        return "empleados", fila, nombre_persona(fila)
    return "", {}, ""


def cliente_de_usuario(usuario: dict) -> dict | None:
    if usuario.get("cliente_id"):
        return read_one("clientes", id=usuario.get("cliente_id"))
    return None


def usuario_por_cliente(cliente_id) -> dict | None:
    return read_one("usuarios", cliente_id=cliente_id)


def tecnicos_info(ids: Iterable) -> dict:
    ids = [i for i in dict.fromkeys(ids or []) if i is not None]
    if not ids:
        return {}
    tecnicos = read_rows("tecnicos", "id,empleado_id,especialidad,nivel", in_filtros={"id": ids})
    empleados_ids = [t.get("empleado_id") for t in tecnicos if t.get("empleado_id")]
    empleados = read_rows("empleados", "id,nombres,apellidos,telefono,correo", in_filtros={"id": empleados_ids})
    emp_map = {e.get("id"): e for e in empleados}
    salida = {}
    for t in tecnicos:
        emp = emp_map.get(t.get("empleado_id")) or {}
        salida[t.get("id")] = {
            "id": t.get("id"),
            "nombre": nombre_persona(emp) or f"Técnico #{t.get('id')}",
            "nivel": t.get("nivel") or "JUNIOR",
            "especialidad": t.get("especialidad") or "GENERAL",
            "telefono": emp.get("telefono"),
            "correo": emp.get("correo"),
        }
    return salida


# ---------------------------------------------------------------------------
# Citas
# ---------------------------------------------------------------------------

def appointments_payload(reservas: list[dict], *, incluir_fotos: bool = True) -> list[dict]:
    """Serializa reservas con todas sus relaciones en pocas consultas."""
    reservas = reservas or []
    if not reservas:
        return []

    def _ids(campo):
        return [r.get(campo) for r in reservas if r.get(campo) is not None]

    clientes = {c.get("id"): c for c in read_rows("clientes", "id,nombres,apellidos,razon_social,telefono,correo", in_filtros={"id": _ids("cliente_id")})}
    equipos = {e.get("id"): e for e in read_rows("electrodomesticos", "id,tipo_id,tipo_otro,marca_id,marca_otro,modelo,numero_serie,color,observaciones", in_filtros={"id": _ids("electrodomestico_id")})}
    tipos = {t.get("id"): t.get("nombre") for t in read_rows("tipos_electrodomestico", "id,nombre", in_filtros={"id": [e.get("tipo_id") for e in equipos.values() if e.get("tipo_id")]})}
    marcas = {m.get("id"): m.get("nombre") for m in read_rows("marcas", "id,nombre", in_filtros={"id": [e.get("marca_id") for e in equipos.values() if e.get("marca_id")]})}
    tecnicos = tecnicos_info(_ids("tecnico_id"))

    metas = {}
    fotos = {}
    if incluir_fotos and reservas:
        refs = [str(r.get("id")) for r in reservas]
        filas = read_rows(
            "notificaciones",
            "id,tipo,referencia,titulo,mensaje",
            in_filtros={"referencia": refs},
            order="id",
        )
        for f in filas:
            if f.get("tipo") == "CITA_META":
                try:
                    metas[f.get("referencia")] = json.loads(f.get("mensaje") or "{}")
                except (TypeError, ValueError):
                    pass
            elif f.get("tipo") == "CITA_FOTO":
                fotos.setdefault(f.get("referencia"), []).append(f.get("titulo"))

    salida = []
    for r in reservas:
        cliente = clientes.get(r.get("cliente_id")) or {}
        equipo = equipos.get(r.get("electrodomestico_id")) or {}
        meta = metas.get(str(r.get("id"))) or {}
        tipo_nombre = texto(equipo.get("tipo_otro")) or tipos.get(equipo.get("tipo_id")) or ""
        marca_nombre = texto(equipo.get("marca_otro")) or marcas.get(equipo.get("marca_id")) or ""
        tecnico = tecnicos.get(r.get("tecnico_id"))
        fotos_urls = list(fotos.get(str(r.get("id")), []))
        for foto_id in meta.get("foto_ids") or []:
            foto = read_one("notificaciones", "titulo", id=foto_id, tipo="CITA_FOTO")
            if foto and foto.get("titulo"):
                fotos_urls.append(foto.get("titulo"))
        salida.append(
            {
                "id": r.get("id"),
                "codigo": f"CIT-{int(r.get('id')):04d}",
                "estado": (meta.get("estado_api") or r.get("estado")),
                "fecha": fecha_iso(r.get("fecha_reserva")),
                "hora_inicio": hhmm(r.get("hora_inicio")),
                "hora_fin": hhmm(r.get("hora_fin")),
                "tipo_atencion": r.get("tipo_atencion"),
                "cliente": {
                    "id": cliente.get("id") or r.get("cliente_id"),
                    "nombre": nombre_persona(cliente) or "Cliente",
                    "telefono": cliente.get("telefono") or "",
                },
                "direccion": r.get("calle") or "",
                "latitud": meta.get("latitud"),
                "longitud": meta.get("longitud"),
                "equipo": {
                    "tipo": tipo_nombre,
                    "marca": marca_nombre,
                    "modelo": equipo.get("modelo") or "",
                    "serie": equipo.get("numero_serie") or "",
                    "color": equipo.get("color") or "",
                },
                "descripcion_problema": r.get("motivo") or "",
                "observaciones": r.get("observaciones") or "",
                "tecnico": (
                    {
                        "id": tecnico.get("id"),
                        "nombre": tecnico.get("nombre"),
                        "nivel": tecnico.get("nivel"),
                    }
                    if tecnico
                    else None
                ),
                "fotos": [u for u in dict.fromkeys(fotos_urls) if u],
                "pdf_url": f"/api/v1/appointments/{r.get('id')}/ticket.pdf",
                "_meta": meta,
            }
        )
    return salida


def cita_por_id(reserva_id: int) -> dict | None:
    return read_one("reservas", "*", id=reserva_id)


def puede_ver_cita(reserva: dict) -> bool:
    ctx = contexto_actual()
    rol = ctx.get("rol")
    if rol in {"ADMINISTRADOR", "SUPERVISOR", "RECEPCIONISTA"}:
        return True
    if rol == "CLIENTE":
        return reserva.get("cliente_id") == ctx.get("cliente_id")
    if rol == "TECNICO":
        tecnico = tecnico_actual()
        return bool(tecnico and reserva.get("tecnico_id") == tecnico.get("id"))
    return False


# ---------------------------------------------------------------------------
# Varios
# ---------------------------------------------------------------------------

def codigo_pedido_siguiente() -> str:
    filas = read_rows("ventas", "id", order="id", desc=True, limit=1)
    n = (int(filas[0].get("id") or 0) + 1) if filas else 1
    return f"SL-{n:04d}"


def tipo_documento_id(codigo: str) -> int:
    codigo = texto(codigo).upper()
    fila = read_one("tipo_documento", "id,codigo,nombre", codigo=codigo)
    if fila:
        return int(fila["id"])
    # Fallback con los ids habituales del proyecto.
    mapa = {"DNI": 1, "RUC": 2, "CE": 3, "PASAPORTE": 4}
    if codigo not in mapa:
        raise ApiError("El tipo de documento no es válido.", 400, "DATOS_INVALIDOS")
    return mapa[codigo]
