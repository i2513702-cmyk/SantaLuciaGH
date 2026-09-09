"""Carrito persistente en Supabase.

Estructura:
  - carritos        -> sesión de carrito, una por usuario o sesión anónima.
  - detalle_carrito -> productos y cantidades del carrito (en la BD desde el
                       primer "Agregar", sin necesidad de comprar).
  - finalizar()     -> convierte el carrito en una venta (ventas + detalle_venta
                       + comprobante) y devuelve el resumen del pedido.

Vigencia:
  - Cada carrito nace con un vencimiento (CART_TTL_HOURS, 3 h por defecto).
  - Al acceder, si el carrito vence, se elimina (detalle + carrito) y se crea
    uno nuevo. `purgar_expirados()` limpia los vencidos desde el backend.
"""

import threading
import uuid
from datetime import datetime, timedelta, timezone

from app.config import Config
from app.exceptions import NotFoundError, ValidationError
from app.supabase_client import error_postgrest, get_admin_client, get_reader

ESTADO_ACTIVO = "ACTIVO"
MAX_CANTIDAD = 99

ESTADO_VENTA = "PENDIENTE_PAGO"
CANAL_VENTA = "TIENDA"
TIPO_ENTREGA = "NO_APLICA"

COMPROBANTE_TIPO = "BOLETA"
COMPROBANTE_SERIE = "B001"
COMPROBANTE_ESTADO = "EMITIDO"

HORAS_VIGENCIA = int(getattr(Config, "CART_TTL_HOURS", 3))


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _vencimiento() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=HORAS_VIGENCIA)).isoformat()


def _utc(valor):
    """Convierte un timestamp de PostgREST a datetime con zona horaria UTC."""
    if isinstance(valor, datetime):
        d = valor
    else:
        try:
            d = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _expirado(fila: dict) -> bool:
    """Compares fecha de expiración; si falta, usa fecha de creación + vigencia."""
    exp = _utc(fila.get("fecha_expiracion"))
    if exp is None:
        creado = _utc(fila.get("fecha_creacion"))
        if creado is None:
            return False
        exp = creado + timedelta(hours=HORAS_VIGENCIA)
    return exp < datetime.now(timezone.utc)


def _precio_producto(prod: dict) -> float:
    """Precio efectivo: promocional si existe, si no, el de venta."""
    try:
        return float(prod.get("precio_promocional") or prod.get("precio_venta") or 0)
    except (TypeError, ValueError):
        return 0.0


def _codigo_pedido() -> str:
    try:
        resp = get_reader().table("ventas").select("id").order("id", desc=True).limit(1).execute()
        n = (resp.data[0]["id"] if resp.data else 0) + 1
    except Exception:  # noqa: BLE001
        n = 1
    return f"PED-{n:04d}"


def nueva_sesion() -> str:
    """Identificador único para diferenciar carritos anónimos."""
    return uuid.uuid4().hex


def _buscar_activo(usuario_sesion: str) -> dict | None:
    try:
        resp = (
            get_reader()
            .table("carritos")
            .select("*")
            .eq("usuario_sesion", usuario_sesion)
            .eq("estado", ESTADO_ACTIVO)
            .limit(1)
            .execute()
        )
    except Exception:  # noqa: BLE001
        return None
    return resp.data[0] if resp.data else None


def crear(usuario_sesion: str) -> int:
    """Crea un carrito activo con vencimiento y devuelve su id."""
    try:
        resp = get_admin_client().table("carritos").insert({
            "usuario_sesion": usuario_sesion,
            "estado": ESTADO_ACTIVO,
            "fecha_creacion": _ahora(),
            "fecha_actualizacion": _ahora(),
            "fecha_expiracion": _vencimiento(),
        }).execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    return resp.data[0]["id"]


def _borrar(carrito_id: int) -> None:
    """Elimina el detalle y el carrito (best-effort para no romper por FKs)."""
    try:
        get_admin_client().table("detalle_carrito").delete().eq("carrito_id", carrito_id).execute()
        get_admin_client().table("carritos").delete().eq("id", carrito_id).execute()
    except Exception:  # noqa: BLE001
        # Si está referenciado por una venta, se conserva marcado como inactivo.
        try:
            get_admin_client().table("carritos").update(
                {"estado": "ABANDONADO", "fecha_actualizacion": _ahora()}
            ).eq("id", carrito_id).execute()
        except Exception:  # noqa: BLE001
            pass


def obtener_carrito_vigente(usuario_sesion: str) -> tuple[int, bool, bool]:
    """Devuelve (carrito_id, es_nuevo, hubo_expiracion).

    Reutiliza el carrito activo del usuario si sigue vigente; si venció, lo
    elimina y crea uno nuevo. El frontend puede usar las banderas para avisar
    al usuario con un mensaje flash.
    """
    carrito = _buscar_activo(usuario_sesion)
    expirado = False

    if carrito and _expirado(carrito):
        _borrar(carrito["id"])
        expirado = True
        carrito = None

    if carrito is None:
        return crear(usuario_sesion), True, expirado
    return carrito["id"], False, expirado


def purgar_expirados() -> int:
    """Borra todos los carritos ACTIVO ya vencidos. Devuelve cuántos eliminó."""
    try:
        resp = (
            get_reader()
            .table("carritos")
            .select("id,fecha_creacion,fecha_expiracion,estado")
            .eq("estado", ESTADO_ACTIVO)
            .execute()
        )
    except Exception:  # noqa: BLE001
        return 0
    eliminados = 0
    for carrito in resp.data or []:
        if _expirado(carrito):
            _borrar(carrito["id"])
            eliminados += 1
    return eliminados


def iniciar_purga():
    """Limpieza de carritos vencidos en segundo plano al arrancar la app."""
    def _tarea():
        try:
            purgar_expirados()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_tarea, daemon=True).start()


def _tocar(carrito_id: int) -> None:
    try:
        get_admin_client().table("carritos").update(
            {"fecha_actualizacion": _ahora()}
        ).eq("id", carrito_id).execute()
    except Exception:  # noqa: BLE001
        pass


def _buscar_detalle(carrito_id: int, producto_id: int) -> dict | None:
    try:
        resp = (
            get_reader()
            .table("detalle_carrito")
            .select("id,cantidad")
            .eq("carrito_id", carrito_id)
            .eq("producto_id", producto_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    return resp.data[0] if resp.data else None


def cantidad(carrito_id: int, producto_id: int) -> int:
    """Cantidad actual de un producto en el carrito (0 si no está)."""
    detalle = _buscar_detalle(carrito_id, producto_id)
    return int(detalle.get("cantidad") or 0) if detalle else 0


def contar_por_usuario(usuario_sesion: str) -> int:
    """Contador de items del carrito activo de un usuario sin crearlo."""
    carrito = _buscar_activo(usuario_sesion)
    if not carrito or _expirado(carrito):
        return 0
    return contar(carrito["id"])


def reclamar(carrito_id: int, token_anon: str, usuario_sesion: str) -> bool:
    """Asocia un carrito anónimo al usuario cuando inicia sesión.

    Solo si el usuario aún no tiene un carrito activo y el carrito realmente
    pertenece a la sesión anónima (token). Devuelve True si se reclamó.
    """
    if _buscar_activo(usuario_sesion):
        return False
    try:
        resp = (
            get_reader()
            .table("carritos")
            .select("id,usuario_sesion")
            .eq("id", carrito_id)
            .limit(1)
            .execute()
        )
        if not resp.data or resp.data[0]["usuario_sesion"] != f"anon:{token_anon}":
            return False
        get_admin_client().table("carritos").update(
            {"usuario_sesion": usuario_sesion}
        ).eq("id", carrito_id).execute()
        return True
    except Exception:  # noqa: BLE001
        return False


def _producto(producto_id: int) -> dict | None:
    try:
        resp = (
            get_reader()
            .table("productos")
            .select("id,nombre,precio_venta,precio_promocional,activo")
            .eq("id", producto_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    return resp.data[0] if resp.data else None


def agregar(carrito_id: int, producto_id: int, cantidad: int) -> dict:
    """Agrega o incrementa un producto en el carrito."""
    if cantidad < 1:
        cantidad = 1

    prod = _producto(producto_id)
    if not prod:
        raise NotFoundError("El producto no existe o fue eliminado.")
    if not prod.get("activo"):
        raise ValidationError("El producto no está activo y no puede agregarse.")

    precio = _precio_producto(prod)
    detalle = _buscar_detalle(carrito_id, producto_id)
    if detalle:
        nueva = min(int(detalle.get("cantidad") or 0) + cantidad, MAX_CANTIDAD)
        try:
            get_admin_client().table("detalle_carrito").update(
                {"cantidad": nueva, "precio_unitario": precio}
            ).eq("id", detalle["id"]).execute()
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(error_postgrest(exc)) from exc
        resultado = {"cantidad": nueva}
    else:
        try:
            get_admin_client().table("detalle_carrito").insert({
                "carrito_id": carrito_id,
                "producto_id": producto_id,
                "cantidad": min(cantidad, MAX_CANTIDAD),
                "precio_unitario": precio,
                "fecha_agregado": _ahora(),
            }).execute()
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(error_postgrest(exc)) from exc
        resultado = {"cantidad": min(cantidad, MAX_CANTIDAD)}

    _tocar(carrito_id)
    return resultado


def listar(carrito_id: int) -> list:
    """Devuelve los items del carrito con datos vivos del producto."""
    try:
        resp = (
            get_reader()
            .table("detalle_carrito")
            .select(
                "id,carrito_id,producto_id,cantidad,"
                "productos(id,nombre,precio_venta,precio_promocional,marcas(nombre),categorias(nombre))"
            )
            .eq("carrito_id", carrito_id)
            .order("id")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc

    items = []
    for r in resp.data or []:
        prod = r.get("productos") or {}
        precio = _precio_producto(prod)
        cantidad = int(r.get("cantidad") or 0)
        subtotal = precio * cantidad
        items.append({
            "detalle_id": r.get("id"),
            "producto_id": r.get("producto_id"),
            "nombre": prod.get("nombre"),
            "marca": (prod.get("marcas") or {}).get("nombre"),
            "precio_unitario": f"S/ {precio:,.2f}",
            "precio_num": precio,
            "cantidad": cantidad,
            "subtotal": f"S/ {subtotal:,.2f}",
            "subtotal_num": subtotal,
        })
    return items


def contar(carrito_id: int) -> int:
    try:
        resp = (
            get_reader()
            .table("detalle_carrito")
            .select("cantidad")
            .eq("carrito_id", carrito_id)
            .execute()
        )
    except Exception:  # noqa: BLE001
        return 0
    return sum(int(r.get("cantidad") or 0) for r in resp.data or [])


def set_cantidad(carrito_id: int, producto_id: int, cantidad: int) -> int:
    """Fija la cantidad de un producto; si es <= 0, lo elimina."""
    detalle = _buscar_detalle(carrito_id, producto_id)
    if not detalle:
        return 0
    if cantidad <= 0:
        try:
            get_admin_client().table("detalle_carrito").delete().eq("id", detalle["id"]).execute()
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(error_postgrest(exc)) from exc
        _tocar(carrito_id)
        return 0
    nueva = min(cantidad, MAX_CANTIDAD)
    try:
        get_admin_client().table("detalle_carrito").update(
            {"cantidad": nueva}
        ).eq("id", detalle["id"]).execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    _tocar(carrito_id)
    return nueva


def vaciar(carrito_id: int) -> None:
    try:
        get_admin_client().table("detalle_carrito").delete().eq("carrito_id", carrito_id).execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    _tocar(carrito_id)


def finalizar(carrito_id: int, usuario_id: int | None = None) -> dict:
    """Convierte el carrito en una venta (ventas + detalle_venta + comprobante)."""
    items = listar(carrito_id)
    if not items:
        raise ValidationError("El carrito está vacío. Agrega productos para continuar.")

    subtotal = round(sum(i["subtotal_num"] for i in items), 2)
    descuento = 0.0
    costo_envio = 0.0
    total = round(subtotal - descuento + costo_envio, 2)

    codigo = _codigo_pedido()
    try:
        venta = get_admin_client().table("ventas").insert({
            "cliente_id": None,
            "usuario_id": usuario_id,
            "carrito_id": carrito_id,
            "codigo_pedido": codigo,
            "canal_venta": CANAL_VENTA,
            "tipo_entrega": TIPO_ENTREGA,
            "fecha_venta": _ahora(),
            "subtotal": subtotal,
            "descuento": descuento,
            "costo_envio": costo_envio,
            "total": total,
            "estado": ESTADO_VENTA,
            "observaciones": None,
        }).execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(
            f"No se pudo registrar el pedido: {error_postgrest(exc)}"
        ) from exc
    venta_id = venta.data[0]["id"]

    try:
        get_admin_client().table("detalle_venta").insert([
            {
                "venta_id": venta_id,
                "producto_id": i["producto_id"],
                "cantidad": i["cantidad"],
                "precio_unitario": i["precio_num"],
                "subtotal": i["subtotal_num"],
                "descuento": 0.0,
            }
            for i in items
        ]).execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc

    try:
        get_admin_client().table("comprobantes").insert({
            "venta_id": venta_id,
            "tipo_comprobante": COMPROBANTE_TIPO,
            "serie": COMPROBANTE_SERIE,
            "numero": f"{venta_id:08d}",
            "total": total,
            "igv": 0.0,
            "estado": COMPROBANTE_ESTADO,
        }).execute()
    except Exception:  # noqa: BLE001
        # El comprobante es informativo; la venta ya está registrada.
        pass

    try:
        get_admin_client().table("carritos").update(
            {"estado": "ABANDONADO", "fecha_actualizacion": _ahora()}
        ).eq("id", carrito_id).execute()
    except Exception:  # noqa: BLE001
        pass

    return {
        "venta_id": venta_id,
        "codigo_pedido": codigo,
        "n_items": sum(i["cantidad"] for i in items),
        "total": f"S/ {total:,.2f}",
    }