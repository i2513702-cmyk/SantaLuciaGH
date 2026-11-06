"""API REST interna para el autocompletado del formulario de clientes.

Devuelve solo los campos necesarios para rellenar el formulario (nombres,
apellidos, teléfono y correo). Nunca expone contraseñas ni datos de otros
clientes, y exige sesión iniciada: la misma política que el resto del portal.
"""

import logging
import re

from flask import Blueprint, jsonify, request

from app.decorators import login_required
from app.exceptions import AppError, ValidationError
from app.services import identidad_api
from app.supabase_client import error_postgrest, get_reader

log = logging.getLogger(__name__)

bp = Blueprint("api", __name__, url_prefix="/api")

# Solo dígitos: un documento nunca lleva letras ni símbolos.
_SOLO_DIGITOS = re.compile(r"^\d+$")

# Longitudes válidas por tipo de documento (Perú). Se usa como validación
# laxa: si el tipo no está en el mapa, se acepta cualquier longitud razonable.
LONGITUD_DOCUMENTO = {
    "DNI": (8, 8),
    "CE": (9, 12),
    "RUC": (11, 11),
    "PASAPORTE": (6, 12),
    "PT": (1, 15),
}

MIN_CARACTERES_BUSQUEDA = 6
MAX_CARACTERES_BUSQUEDA = 15

CAMPOS_CLIENTE = "id,nombres,apellidos,razon_social,telefono,correo,numero_documento"


def _codigo_tipo_documento() -> str:
    """Código del tipo de documento seleccionado (DNI, RUC, ...)."""
    tipo_id = (request.args.get("tipo_documento_id") or "").strip()
    if not tipo_id.isdigit():
        return ""
    try:
        fila = (
            get_reader()
            .table("tipo_documento")
            .select("codigo")
            .eq("id", int(tipo_id))
            .limit(1)
            .execute()
        ).data
    except Exception:  # noqa: BLE001
        return ""
    return ((fila or [{}])[0].get("codigo") or "").strip().upper()


def _validar_documento(numero: str, codigo_tipo: str) -> str:
    """Devuelve '' si el documento es válido, o el mensaje de error."""
    if not _SOLO_DIGITOS.match(numero):
        return "El número de documento solo puede contener dígitos."

    minimo, maximo = LONGITUD_DOCUMENTO.get(codigo_tipo, (MIN_CARACTERES_BUSQUEDA, MAX_CARACTERES_BUSQUEDA))
    if len(numero) < minimo or len(numero) > maximo:
        esperado = str(minimo) if minimo == maximo else f"{minimo} y {maximo}"
        etiqueta = codigo_tipo or "documento"
        return f"Un {etiqueta} debe tener entre {esperado} dígitos."
    return ""


def _normalizar(cliente: dict) -> dict:
    """Datos del cliente listos para el formulario."""
    return {
        "id": cliente.get("id"),
        "nombres": cliente.get("nombres") or "",
        "apellidos": cliente.get("apellidos") or "",
        "razon_social": cliente.get("razon_social") or "",
        "telefono": cliente.get("telefono") or "",
        "correo": cliente.get("correo") or "",
        "numero_documento": cliente.get("numero_documento") or "",
    }


def _inferir_tipo(numero: str, codigo_tipo: str) -> str:
    """Tipo consultable en la API externa (DNI/RUC/CE); '' si no se puede."""
    if codigo_tipo in ("DNI", "RUC", "CE"):
        return codigo_tipo
    if codigo_tipo:  # pasaporte u otro: no hay API de consulta
        return ""
    if len(numero) == 8:
        return "DNI"
    if len(numero) == 11:
        return "RUC"
    return ""


def _desde_api(numero: str, tipo: str) -> dict:
    """Consulta RENIEC/SUNAT (apiperu.dev / json.pe) y lo deja listo para el formulario."""
    datos = identidad_api.consultar(tipo, numero)
    if tipo == "RUC":
        nombres = datos.get("razon_social") or datos.get("nombre_completo") or ""
        apellidos = ""
    else:
        nombres = datos.get("nombres") or ""
        apellidos = " ".join(
            p for p in (datos.get("apellido_paterno"), datos.get("apellido_materno")) if p
        )
        if not nombres and not apellidos:
            nombres = datos.get("nombre_completo") or ""
    return {
        "id": None,
        "nombres": nombres,
        "apellidos": apellidos,
        "razon_social": datos.get("razon_social") or "",
        "telefono": "",
        "correo": "",
        "numero_documento": numero,
    }


@bp.get("/clientes/buscar/<numero_documento>")
@login_required
def buscar_cliente(numero_documento):
    """GET /api/clientes/buscar/<documento> — autocompletado por documento.

    200 -> cliente encontrado (se rellenan los campos del formulario).
    404 -> no existe: el usuario puede escribir sus datos manualmente.
    400 -> documento incompleto o con formato inválido: no se consulta la BD.
    502 -> Supabase no respondió o la tabla/clave no es válida.
    """
    numero = (numero_documento or "").strip()
    codigo_tipo = _codigo_tipo_documento()

    if len(numero) < MIN_CARACTERES_BUSQUEDA:
        return jsonify({
            "ok": False,
            "encontrado": False,
            "detalle": "El número de documento está incompleto.",
        }), 400

    if len(numero) > MAX_CARACTERES_BUSQUEDA:
        return jsonify({
            "ok": False,
            "encontrado": False,
            "detalle": "El número de documento es demasiado largo.",
        }), 400

    error = _validar_documento(numero, codigo_tipo)
    if error:
        return jsonify({"ok": False, "encontrado": False, "detalle": error}), 400

    try:
        consulta = get_reader().table("clientes").select(CAMPOS_CLIENTE).eq("numero_documento", numero)
        if codigo_tipo and request.args.get("tipo_documento_id", "").strip().isdigit():
            consulta = consulta.eq("tipo_documento_id", int(request.args["tipo_documento_id"]))
        fila = consulta.limit(1).execute().data
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "ok": False,
            "encontrado": False,
            "detalle": error_postgrest(exc),
        }), 502

    if not fila:
        # No esta en la BD: se consulta RENIEC/SUNAT para autocompletar igual.
        tipo_api = _inferir_tipo(numero, codigo_tipo)
        if tipo_api:
            try:
                cliente = _desde_api(numero, tipo_api)
                if cliente["nombres"] or cliente["apellidos"]:
                    return jsonify({
                        "ok": True,
                        "encontrado": False,
                        "origen": "api",
                        "cliente": cliente,
                    }), 200
            except (AppError, ValidationError) as exc:
                log.warning("Consulta externa %s %s fallo: %s", tipo_api, numero, exc)
            except Exception:  # noqa: BLE001
                log.exception("Consulta externa %s %s fallo", tipo_api, numero)
        return jsonify({
            "ok": True,
            "encontrado": False,
            "detalle": "No se encontraron datos del documento. Completa tus datos manualmente.",
        }), 404

    return jsonify({
        "ok": True,
        "encontrado": True,
        "cliente": _normalizar(fila[0]),
    }), 200

# ==============================================================================
# ENDPOINTS ADAPTADOS A LA ARQUITECTURA ACTUAL DEL SISTEMA (SANTA LUCÍA)
# ==============================================================================
# Nota: Respetan e integran los endpoints preexistentes del proyecto:
#   - /api/clientes/buscar/<numero_documento>
#   - /identidad/consultar y /identidad/consultar-empleado
#   - /citas/disponibilidad
#   - /admin/kpis/datos
#   - /admin/registro/<tabla>
#   - /worker/resumen/<clave>
#   - /supabase/status y /supabase/health
# ==============================================================================

from datetime import datetime, timezone
from flask import current_app, session, url_for
from app.decorators import ROLES_DB, roles_required, ventas_required
from app.services import (
    agenda_service,
    carrito_service,
    cita_service,
    cuenta_guard,
    inventario_service,
    libro_reclamaciones_service,
    login_guard,
    producto_service,
    reporte_service,
    rest_auth_service,
    storage_service,
    tracking_service,
)


# --- 1. MÓDULO PÚBLICO: INICIO Y TIENDA (/ , /tienda , /producto/<id>) ---

@bp.get("/inicio")
def api_inicio():
    """GET /api/inicio — Datos consolidados de la portada (marcas, categorías y destacados)."""
    from app.routes.main import _cargar_home_data
    data = _cargar_home_data()
    return jsonify({
        "ok": True,
        "marcas": data.get("marcas", []),
        "categorias": data.get("categorias", []),
        "productos_destacados": data.get("productos_destacados", []),
        "contacto": {
            "telefono": current_app.config.get("CONTACTO_TELEFONO", "+51 964 618 892"),
            "correo": current_app.config.get("CONTACTO_CORREO", "contacto@santalucia.pe"),
            "direccion": current_app.config.get("CONTACTO_DIRECCION", "Av. 28 de Julio 723 · Chilca, Huancayo"),
        },
    }), 200


@bp.get("/servicios")
def api_servicios():
    """GET /api/servicios — Catálogo de servicios técnicos del taller."""
    return jsonify({
        "ok": True,
        "servicios": [
            {"id": 1, "equipo": "Lavadoras", "descripcion": "Cambio de rodamientos, bombas de desagüe, tarjetas electrónicas y mantenimiento.", "garantia_dias": 90},
            {"id": 2, "equipo": "Refrigeradoras", "descripcion": "Carga de gas refrigerante, cambio de compresor, sensores y sistema No Frost.", "garantia_dias": 90},
            {"id": 3, "equipo": "Secadoras", "descripcion": "Reemplazo de fajas, resistencias térmicas, sensores de humedad y ductos.", "garantia_dias": 90},
            {"id": 4, "equipo": "Congeladoras", "descripcion": "Sellado de fugas, motor ventilador, relé de arranque y control térmico.", "garantia_dias": 90},
            {"id": 5, "equipo": "Hornos Microondas", "descripcion": "Cambio de magnetrón, fusibles de alta tensión, membrana táctil y plato.", "garantia_dias": 90},
        ],
    }), 200


@bp.get("/tienda/marcas")
def api_tienda_marcas():
    """GET /api/tienda/marcas — Listado de marcas activas en el catálogo."""
    try:
        marcas = get_reader().table("marcas").select("id,nombre").order("nombre").execute().data or []
    except Exception:
        marcas = []
    return jsonify({"ok": True, "marcas": marcas, "total": len(marcas)}), 200


@bp.get("/tienda/categorias")
def api_tienda_categorias():
    """GET /api/tienda/categorias — Listado de categorías con conteo de productos."""
    try:
        categorias = get_reader().table("categorias").select("id,nombre").order("nombre").execute().data or []
        productos = get_reader().table("productos").select("id,categoria_id").eq("activo", True).execute().data or []
    except Exception:
        categorias, productos = [], []
    conteo = {}
    for p in productos:
        cid = p.get("categoria_id")
        conteo[cid] = conteo.get(cid, 0) + 1
    for c in categorias:
        c["total_productos"] = conteo.get(c["id"], 0)
    return jsonify({"ok": True, "categorias": categorias, "total": len(categorias)}), 200


@bp.get("/tienda/productos")
def api_tienda_productos():
    """GET /api/tienda/productos — Búsqueda y filtrado de productos de la tienda."""
    from app.routes.main import _leer_productos_activos, _mapear_producto
    q = (request.args.get("q") or "").strip().lower()
    cat_id = (request.args.get("cat") or "").strip()
    if cat_id.lower() in ("", "todas", "all", "todo"):
        cat_id = ""
    marca_id = (request.args.get("marca") or "").strip()
    solo_ofertas = request.args.get("oferta") in ("on", "true", "1")

    productos = [_mapear_producto(p) for p in _leer_productos_activos()]
    resultados = [
        p for p in productos
        if (not marca_id or str(p.get("marca_id") or "") == marca_id)
        and (not cat_id or str(p.get("categoria_id") or "") == cat_id)
        and (not solo_ofertas or p.get("oferta"))
        and (not q or any(q in (str(t or "")).lower() for t in (
            p.get("nombre"), p.get("descripcion"), p.get("modelo"), p.get("marca"), p.get("categoria"), p.get("codigo")
        )))
    ]
    return jsonify({
        "ok": True,
        "total": len(resultados),
        "filtros": {"q": q, "cat": cat_id, "marca": marca_id, "oferta": solo_ofertas},
        "productos": resultados,
    }), 200


@bp.get("/producto/<int:producto_id>")
def api_producto_detalle(producto_id: int):
    """GET /api/producto/<id> — Ficha detallada del producto y su galería."""
    from app.routes.main import _leer_producto_por_id, _leer_productos_activos, _mapear_producto
    fila = _leer_producto_por_id(producto_id)
    if not fila:
        return jsonify({"ok": False, "detalle": "Producto no encontrado."}), 404
    producto = _mapear_producto(fila)
    stock_info = inventario_service.stock_por_producto([producto_id])
    producto["stock_disponible"] = stock_info.get("stock", {}).get(producto_id, 0)
    try:
        galeria = [f["url"] for f in storage_service.listar_fotos_producto(producto_id, producto.get("imagen_url"))]
    except Exception:
        galeria = [producto["imagen_url"]] if producto.get("imagen_url") else []
    relacionados = [
        _mapear_producto(p)
        for p in _leer_productos_activos()
        if p.get("id") != producto_id and p.get("categoria_id") == fila.get("categoria_id")
    ][:4]
    return jsonify({
        "ok": True,
        "producto": producto,
        "galeria": galeria,
        "relacionados": relacionados,
    }), 200


# --- 2. MÓDULO SESIÓN, AUTENTICACIÓN Y PERFIL (/login, /registrarse, /perfil) ---

@bp.get("/sesion")
def api_sesion():
    """GET /api/sesion — Estado actual de la sesión del usuario autenticado o visitante."""
    uid = session.get("usuario_id")
    rol = session.get("rol")
    if not uid:
        return jsonify({
            "ok": True,
            "autenticado": False,
            "rol": None,
            "carrito_n": session.get("carrito_n", 0),
        }), 200
    return jsonify({
        "ok": True,
        "autenticado": True,
        "usuario_id": uid,
        "username": session.get("username"),
        "nombre": session.get("nombre"),
        "rol": rol,
        "rol_label": ROLES_DB.get(rol or "", rol),
        "carrito_n": session.get("carrito_n", 0),
    }), 200


@bp.get("/auth/disponibilidad")
def api_auth_disponibilidad():
    """GET /api/auth/disponibilidad — Verifica si usuario, correo o documento está disponible."""
    campo = (request.args.get("campo") or "nombre_usuario").strip().lower()
    valor = (request.args.get("valor") or "").strip()
    if not valor:
        return jsonify({"ok": True, "disponible": True}), 200
    rd = get_reader()
    try:
        if campo in ("usuario", "nombre_usuario"):
            filas = rd.table("usuarios").select("id").eq("nombre_usuario", valor).limit(1).execute().data
        elif campo == "correo":
            filas = rd.table("usuarios").select("id").eq("correo", valor.lower()).limit(1).execute().data
        else:
            filas = rd.table("clientes").select("id").eq("numero_documento", valor).limit(1).execute().data
        return jsonify({"ok": True, "campo": campo, "valor": valor, "disponible": not bool(filas)}), 200
    except Exception:
        return jsonify({"ok": True, "disponible": True}), 200


@bp.get("/perfil")
@login_required
def api_perfil():
    """GET /api/perfil — Datos personales y cuenta del usuario con sesión activa."""
    uid = session.get("usuario_id")
    try:
        perfil = rest_auth_service.obtener_perfil(uid) or {}
    except Exception:
        perfil = {}
    persona = perfil.get("clientes") or perfil.get("empleados") or {}
    return jsonify({
        "ok": True,
        "usuario": {
            "id": uid,
            "nombre_usuario": perfil.get("nombre_usuario") or session.get("username"),
            "correo": perfil.get("correo") or persona.get("correo") or "",
            "rol": perfil.get("rol") or session.get("rol"),
            "ultimo_acceso": perfil.get("ultimo_acceso"),
        },
        "datos_personales": {
            "nombres": persona.get("nombres") or "",
            "apellidos": persona.get("apellidos") or "",
            "numero_documento": persona.get("numero_documento") or "",
            "telefono": persona.get("telefono") or "",
            "cargo": persona.get("cargo") or "",
        },
    }), 200


# --- 3. MÓDULO CARRITO Y CHECKOUT (/carrito, /checkout) ---

@bp.get("/carrito")
def api_carrito():
    """GET /api/carrito — Estado actual del carrito persistente en Supabase."""
    clave = tracking_service.clave_sesion()
    carrito_id, _, hubo_expiracion = carrito_service.obtener_carrito_vigente(clave)
    session["carrito_id"] = carrito_id
    items = carrito_service.listar(carrito_id)
    session["carrito_n"] = carrito_service.contar(carrito_id)
    subtotal = round(sum(float(i.get("subtotal_num") or 0) for i in items), 2)
    igv = round(subtotal * carrito_service.IGV_TASA, 2)
    total = round(subtotal + igv, 2)
    return jsonify({
        "ok": True,
        "carrito_id": carrito_id,
        "hubo_expiracion": hubo_expiracion,
        "vigencia_horas": carrito_service.HORAS_VIGENCIA,
        "n_items": session["carrito_n"],
        "items": items,
        "subtotal": subtotal,
        "igv": igv,
        "total": total,
    }), 200


@bp.get("/checkout/resumen")
@login_required
def api_checkout_resumen():
    """GET /api/checkout/resumen — Resumen previo al pago, métodos de pago y tipos de documento."""
    clave = tracking_service.clave_sesion()
    carrito_id = session.get("carrito_id")
    if not carrito_id:
        carrito_id, _, _ = carrito_service.obtener_carrito_vigente(clave)
        session["carrito_id"] = carrito_id
    items = carrito_service.listar(carrito_id)
    subtotal = round(sum(float(i.get("subtotal_num") or 0) for i in items), 2)
    igv = round(subtotal * carrito_service.IGV_TASA, 2)
    total = round(subtotal + igv, 2)
    return jsonify({
        "ok": True,
        "carrito_id": carrito_id,
        "items": items,
        "subtotal": subtotal,
        "igv": igv,
        "total": total,
        "metodos_pago": carrito_service.listar_metodos_pago(),
        "tipos_documento": rest_auth_service.listar_tipos_documento(),
        "comprobantes": ["BOLETA", "FACTURA"],
    }), 200


# --- 4. MÓDULO CITAS Y PEDIDOS CLIENTE (/citas, /pedidos) ---

@bp.get("/citas/catalogos")
@login_required
def api_citas_catalogos():
    """GET /api/citas/catalogos — Listas desplegables del formulario de agendamiento."""
    from app.routes.citas import _referencias
    refs = _referencias()
    return jsonify({
        "ok": True,
        "tipos_electrodomestico": refs.get("tipos", []),
        "marcas": refs.get("marcas", []),
        "tipos_documento": refs.get("documentos", []),
        "horarios_base": refs.get("horarios", []),
        "distritos": cita_service.DISTRITOS_DOMICILIO,
        "tipos_atencion": ["TALLER", "DOMICILIO"],
        "cliente_actual": cita_service.datos_usuario(session.get("usuario_id")),
    }), 200


@bp.get("/citas/mis-citas")
@login_required
def api_mis_citas():
    """GET /api/citas/mis-citas — Historial y estado de seguimiento de citas del cliente."""
    perfil = {}
    try:
        perfil = rest_auth_service.obtener_perfil(session.get("usuario_id")) or {}
    except Exception:
        pass
    cliente_id = perfil.get("cliente_id")
    datos = cita_service.listar_citas_cliente(cliente_id) if cliente_id else {"citas": [], "activas": 0, "total": 0}
    return jsonify({"ok": True, "cliente_vinculado": bool(cliente_id), **datos}), 200


@bp.get("/citas/<int:reserva_id>")
@login_required
def api_cita_detalle(reserva_id: int):
    """GET /api/citas/<id> — Detalle de una cita confirmada."""
    try:
        filas = (
            get_reader()
            .table("reservas")
            .select(
                "id,cliente_id,electrodomestico_id,tipo_atencion,fecha_reserva,"
                "hora_inicio,hora_fin,estado,motivo,observaciones,calle,"
                "electrodomesticos(modelo,numero_serie,tipo_otro,marca_otro,marcas(nombre),tipos_electrodomestico(nombre))"
            )
            .eq("id", reserva_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        filas = []
    if not filas:
        return jsonify({"ok": False, "detalle": "Cita no encontrada."}), 404
    return jsonify({"ok": True, "reserva": filas[0]}), 200


@bp.get("/pedidos/mis-pedidos")
@login_required
def api_mis_pedidos():
    """GET /api/pedidos/mis-pedidos — Historial de compras del cliente autenticado."""
    uid = session.get("usuario_id")
    try:
        ventas = (
            get_reader()
            .table("ventas")
            .select("id,codigo_pedido,fecha_venta,subtotal,costo_envio,total,estado,metodo_pago_id,tipo_entrega")
            .eq("usuario_id", uid)
            .order("fecha_venta", desc=True)
            .execute()
            .data
            or []
        )
    except Exception:
        ventas = []
    return jsonify({"ok": True, "total": len(ventas), "pedidos": ventas}), 200


@bp.get("/pedidos/<int:venta_id>")
@login_required
def api_pedido_detalle(venta_id: int):
    """GET /api/pedidos/<id> — Detalle de un pedido, líneas y estado de pago."""
    uid = session.get("usuario_id")
    rol = session.get("rol")
    rd = get_reader()
    try:
        filas = rd.table("ventas").select("*").eq("id", venta_id).limit(1).execute().data or []
    except Exception:
        filas = []
    if not filas:
        return jsonify({"ok": False, "detalle": "Pedido no encontrado."}), 404
    venta = filas[0]
    if rol == "CLIENTE" and venta.get("usuario_id") != uid:
        return jsonify({"ok": False, "detalle": "No autorizado."}), 403
    items = rd.table("detalle_venta").select("*,productos(codigo,nombre)").eq("venta_id", venta_id).execute().data or []
    pagos = rd.table("pagos").select("*").eq("venta_id", venta_id).execute().data or []
    return jsonify({"ok": True, "venta": venta, "items": items, "pagos": pagos}), 200


# --- 5. MÓDULO ADMINISTRACIÓN (/admin/*, /sistema/usuarios, /ventas) ---

@bp.get("/admin/dashboard")
@roles_required("admin")
def api_admin_dashboard():
    """GET /api/admin/dashboard — Métricas consolidadas del panel de administración."""
    from app.routes.admin import TABLAS_CONTEO, _contar, _metricas_dashboard
    conteos = {t: _contar(t) or 0 for t in TABLAS_CONTEO}
    metricas = _metricas_dashboard()
    return jsonify({
        "ok": True,
        "conteos": conteos,
        "metricas": metricas,
        "sincronizado_en": datetime.now(timezone.utc).isoformat(),
    }), 200


@bp.get("/admin/citas")
@roles_required("admin")
def api_admin_citas():
    """GET /api/admin/citas — Listado paginado de citas y técnicos disponibles para asignación."""
    from app.routes.admin import POR_PAGINA_CITAS, _listar_tecnicos
    pagina = max(request.args.get("p", 1, type=int) or 1, 1)
    filtro_tecnico = (request.args.get("tecnico_id") or "").strip()
    try:
        query = (
            get_reader()
            .table("reservas")
            .select(
                "id,fecha_reserva,hora_inicio,hora_fin,estado,tipo_atencion,motivo,tecnico_id,"
                "clientes(nombres,apellidos,telefono,numero_documento,razon_social),"
                "electrodomesticos(modelo,numero_serie,marcas(nombre),tipos_electrodomestico(nombre)),"
                "tecnicos(id,empleados(nombres,apellidos))",
                count="exact",
            )
        )
        if filtro_tecnico == "sin_asignar":
            query = query.is_("tecnico_id", "null")
        elif filtro_tecnico.isdigit():
            query = query.eq("tecnico_id", int(filtro_tecnico))
        resp = (
            query.order("fecha_reserva", desc=True)
            .order("hora_inicio", desc=True)
            .range((pagina - 1) * POR_PAGINA_CITAS, pagina * POR_PAGINA_CITAS - 1)
            .execute()
        )
        filas, total = resp.data or [], resp.count or 0
    except Exception:
        filas, total = [], 0
    return jsonify({
        "ok": True,
        "pagina": pagina,
        "por_pagina": POR_PAGINA_CITAS,
        "total": total,
        "citas": filas,
        "tecnicos": _listar_tecnicos(),
    }), 200


@bp.get("/admin/productos")
@roles_required("admin")
def api_admin_productos():
    """GET /api/admin/productos — Catálogo administrativo con stock físico y reservado."""
    q = (request.args.get("q") or "").strip()
    pagina = max(request.args.get("p", 1, type=int) or 1, 1)
    res = producto_service.listar_productos(q=q, pagina=pagina)
    productos_lista = res.get("productos", [])
    ids = [p.get("id") for p in productos_lista if p.get("id") is not None]
    datos_stock = inventario_service.stock_por_producto(ids) if ids else {"stock": {}, "reservado": {}}
    for prod in productos_lista:
        pid = prod.get("id")
        prod["stock_actual"] = datos_stock.get("stock", {}).get(pid, 0)
        prod["stock_reservado"] = datos_stock.get("reservado", {}).get(pid, 0)
    return jsonify({
        "ok": True,
        "pagina": res.get("pagina", 1),
        "paginas": res.get("paginas", 1),
        "total": res.get("total", 0),
        "productos": productos_lista,
    }), 200


@bp.get("/admin/usuarios")
@roles_required("admin")
def api_admin_usuarios():
    """GET /api/admin/usuarios — Usuarios del sistema filtrados y paginados."""
    nombre = (request.args.get("nombre") or "").strip()
    q = (request.args.get("q") or "").strip()
    rol = (request.args.get("rol") or "").strip()
    pagina = max(request.args.get("p", 1, type=int) or 1, 1)
    res = rest_auth_service.listar_usuarios(nombre=nombre, q=q, rol=rol, pagina=pagina)
    return jsonify({"ok": True, **res}), 200


@bp.get("/admin/seguridad")
@roles_required("admin")
def api_admin_seguridad():
    """GET /api/admin/seguridad — Estado de IPs bloqueadas, cuentas bloqueadas y eventos de login."""
    ips = login_guard.listar()
    return jsonify({
        "ok": True,
        "ips": ips,
        "n_bloqueadas": sum(1 for i in ips if i.get("bloqueada")),
        "cuentas_bloqueadas": cuenta_guard.listar(),
        "eventos": login_guard.listar_eventos(50),
    }), 200


@bp.get("/admin/reportes/<tipo>")
@roles_required("admin")
def api_admin_reportes(tipo: str):
    """GET /api/admin/reportes/<tipo> — Datos JSON del reporte administrativo (ventas, productos, inventario, citas)."""
    if tipo not in reporte_service.REPORTES:
        return jsonify({"ok": False, "detalle": "Tipo de reporte inválido."}), 404
    definicion = reporte_service.REPORTES[tipo]
    desde, hasta, error_periodo = reporte_service.parsear_periodo(
        request.args.get("desde") if definicion["usa_periodo"] else None,
        request.args.get("hasta") if definicion["usa_periodo"] else None,
    )
    if error_periodo:
        return jsonify({"ok": False, "detalle": error_periodo}), 400
    try:
        rep = reporte_service.generar(tipo, desde, hasta)
        return jsonify({"ok": True, "tipo": tipo, "desde": desde.isoformat(), "hasta": hasta.isoformat(), "reporte": rep}), 200
    except Exception as exc:
        return jsonify({"ok": False, "detalle": str(exc)}), 503


@bp.get("/admin/configuracion")
@roles_required("admin")
def api_admin_configuracion():
    """GET /api/admin/configuracion — Estado actual de recursos gráficos en Supabase Storage."""
    return jsonify({
        "ok": True,
        "logo_url": storage_service.obtener_logo_url(),
        "logo_default": url_for("static", filename="img/logo.svg"),
        "fondo_url": storage_service.obtener_fondo_url(),
    }), 200


@bp.get("/admin/reclamos")
@roles_required("admin")
def api_admin_reclamos():
    """GET /api/admin/reclamos — Listado de reclamos y quejas del Libro de Reclamaciones."""
    estado = (request.args.get("estado") or "").strip() or None
    tipo = (request.args.get("tipo") or "").strip() or None
    q = (request.args.get("q") or "").strip() or None
    pagina = max(request.args.get("p", 1, type=int) or 1, 1)
    datos = libro_reclamaciones_service.listar_reclamos(estado=estado, tipo=tipo, busqueda=q, pagina=pagina)
    return jsonify({"ok": True, **datos}), 200


@bp.get("/ventas")
@ventas_required
def api_ventas_listado():
    """GET /api/ventas — Listado de ventas y órdenes para mostrador/administración."""
    pagina = max(request.args.get("p", 1, type=int) or 1, 1)
    por_pagina = 20
    q = (request.args.get("q") or "").strip()
    estado = (request.args.get("estado") or "").strip()
    canal = (request.args.get("canal") or "").strip()
    try:
        builder = get_reader().table("ventas").select(
            "id,codigo_pedido,fecha_venta,estado,canal_venta,tipo_entrega,total,cliente_id,usuario_id",
            count="exact",
        )
        if q:
            builder = builder.ilike("codigo_pedido", f"%{q}%")
        if estado:
            builder = builder.eq("estado", estado)
        if canal:
            builder = builder.eq("canal_venta", canal)
        r = builder.order("fecha_venta", desc=True).range((pagina - 1) * por_pagina, pagina * por_pagina - 1).execute()
        filas, total = r.data or [], r.count or 0
    except Exception:
        filas, total = [], 0
    return jsonify({"ok": True, "pagina": pagina, "total": total, "ventas": filas}), 200


@bp.get("/ventas/reservas")
@ventas_required
def api_ventas_reservas():
    """GET /api/ventas/reservas — Pedidos en efectivo con stock reservado pendiente de cobro."""
    from app.routes.ventas import _ids_metodo_efectivo
    ids_metodo = _ids_metodo_efectivo()
    pagos = []
    if ids_metodo:
        try:
            pagos = (
                get_reader().table("pagos").select("id,venta_id,monto,fecha_creacion")
                .eq("estado", "PENDIENTE").in_("metodo_pago_id", ids_metodo).execute().data or []
            )
        except Exception:
            pagos = []
    return jsonify({"ok": True, "total": len(pagos), "reservas_pendientes": pagos}), 200


# --- 6. MÓDULO PANEL TÉCNICO / WORKER (/worker/*) ---

@bp.get("/worker/dashboard")
@roles_required("worker")
def api_worker_dashboard():
    """GET /api/worker/dashboard — Resumen operativo del técnico/personal logueado."""
    from app.routes.worker import _citas_tecnico, _disponibilidad_tecnico, _tecnico_actual, _tecnico_id
    tecnico = _tecnico_actual()
    tecnico_id = _tecnico_id() or (tecnico or {}).get("id")
    citas = _citas_tecnico(tecnico_id, incluir_libres=True) if tecnico_id else []
    disponibilidad = _disponibilidad_tecnico(tecnico_id) if tecnico_id else []
    return jsonify({
        "ok": True,
        "tecnico": tecnico,
        "resumen": {
            "citas_asignadas": sum(1 for c in citas if c.get("mia")),
            "citas_libres": sum(1 for c in citas if not c.get("mia")),
            "sesiones_disponibilidad": len(disponibilidad),
        },
        "citas_recientes": citas[:10],
        "disponibilidad_reciente": disponibilidad[:10],
    }), 200


@bp.get("/worker/citas")
@roles_required("worker")
def api_worker_citas():
    """GET /api/worker/citas — Citas asignadas al técnico y citas pendientes libres."""
    from app.routes.worker import _citas_tecnico, _tecnico_actual, _tecnico_id
    tecnico = _tecnico_actual()
    tecnico_id = _tecnico_id() or (tecnico or {}).get("id")
    citas = _citas_tecnico(tecnico_id, incluir_libres=True) if tecnico_id else []
    return jsonify({
        "ok": True,
        "tecnico_id": tecnico_id,
        "total": len(citas),
        "n_libres": sum(1 for c in citas if not c.get("mia")),
        "citas": citas,
    }), 200


@bp.get("/worker/reparaciones")
@roles_required("worker")
def api_worker_reparaciones():
    """GET /api/worker/reparaciones — Órdenes de servicio (OS) asignadas al técnico."""
    from app.routes.worker import _tecnico_actual, _tecnico_id
    tecnico = _tecnico_actual()
    tecnico_id = _tecnico_id() or (tecnico or {}).get("id")
    reparaciones = []
    if tecnico_id:
        try:
            reparaciones = (
                get_reader()
                .table("reparaciones")
                .select("*")
                .eq("tecnico_id", tecnico_id)
                .order("fecha_ingreso", desc=True)
                .execute()
            ).data or []
        except Exception:
            reparaciones = []
    return jsonify({"ok": True, "tecnico_id": tecnico_id, "total": len(reparaciones), "reparaciones": reparaciones}), 200


@bp.get("/worker/reparaciones/<int:reparacion_id>")
@roles_required("worker")
def api_worker_reparacion_detalle(reparacion_id: int):
    """GET /api/worker/reparaciones/<id> — Detalle técnico de la orden de servicio, fotos e historial."""
    from app.routes.worker import _cargar_reparacion, _datos_equipo_cliente
    rep = _cargar_reparacion(reparacion_id)
    if not rep:
        return jsonify({"ok": False, "detalle": "Reparación no encontrada."}), 404
    equipo, cliente = _datos_equipo_cliente(rep.get("electrodomestico_id"))
    try:
        fotos = (
            get_reader().table("evidencias_reparacion")
            .select("id,url_archivo,descripcion")
            .eq("reparacion_id", reparacion_id).eq("tipo", "FOTO")
            .order("fecha_creacion", desc=True).execute()
        ).data or []
    except Exception:
        fotos = []
    try:
        historial = (
            get_reader().table("historial_reparacion")
            .select("estado_anterior,estado_nuevo,comentario,fecha")
            .eq("reparacion_id", reparacion_id)
            .order("fecha", desc=True).limit(30).execute()
        ).data or []
    except Exception:
        historial = []
    return jsonify({
        "ok": True,
        "reparacion": rep,
        "equipo": equipo,
        "cliente": cliente,
        "fotos": fotos,
        "historial": historial,
    }), 200


@bp.get("/worker/disponibilidad")
@roles_required("worker")
def api_worker_disponibilidad():
    """GET /api/worker/disponibilidad — Sesiones de agenda_tecnicos del técnico autenticado."""
    from app.routes.worker import _disponibilidad_tecnico, _tecnico_actual, _tecnico_id
    tecnico = _tecnico_actual()
    tecnico_id = _tecnico_id() or (tecnico or {}).get("id")
    sesiones = _disponibilidad_tecnico(tecnico_id) if tecnico_id else []
    return jsonify({
        "ok": True,
        "tecnico_id": tecnico_id,
        "total": len(sesiones),
        "disponibilidad": sesiones,
    }), 200
