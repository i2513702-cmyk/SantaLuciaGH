"""CRUD de productos contra Supabase (módulo administrador).

Lecturas con el cliente que omite RLS (get_reader) y escrituras con la
service_role key (get_admin_client), igual que el resto del proyecto.
"""

from app.exceptions import NotFoundError, ValidationError
from app.supabase_client import error_postgrest, get_admin_client, get_reader

TIPO_PRODUCTOS = ("REPUESTO", "PRODUCTO")

SELECT_PRODUCTO = (
    "id,codigo,nombre,descripcion,tipo_producto,marca_id,categoria_id,modelo,"
    "precio_compra,precio_venta,precio_promocional,unidad_medida,activo,"
    "marcas(nombre),categorias(nombre)"
)


def _existe(tabla: str, id_valor: int) -> bool:
    try:
        resp = get_reader().table(tabla).select("id").eq("id", id_valor).limit(1).execute()
        return bool(resp.data)
    except Exception:  # noqa: BLE001
        return False


def _precio(valor):
    if valor is None or valor == "":
        return None
    try:
        return round(float(valor), 2)
    except (TypeError, ValueError):
        raise ValidationError("Los valores de precio deben ser números.")


def _siguiente_codigo() -> str:
    """Genera un código tipo PRO-00N usando el id más alto existente."""
    try:
        resp = get_reader().table("productos").select("id").order("id", desc=True).limit(1).execute()
        n = (resp.data[0]["id"] if resp.data else 0) + 1
        return f"PRO-{n:03d}"
    except Exception:  # noqa: BLE001
        return "PRO-001"


def _validar_y_mapear(datos) -> dict:
    """Valida el formulario y lo convierte en el dict que espera PostgREST."""
    nombre = (datos.get("nombre") or "").strip()
    if not nombre:
        raise ValidationError("El nombre del producto es obligatorio.")

    venta = _precio(datos.get("precio_venta"))
    if venta is None or venta < 0:
        raise ValidationError("El precio de venta es obligatorio y no puede ser negativo.")

    compra = _precio(datos.get("precio_compra")) or 0.0
    promo = _precio(datos.get("precio_promocional"))

    tipo = (datos.get("tipo_producto") or "").strip().upper()
    if tipo not in TIPO_PRODUCTOS:
        tipo = "REPUESTO"

    unidad = (datos.get("unidad_medida") or "").strip().upper() or "UNIDAD"

    try:
        marca_id = int(datos.get("marca_id") or 0)
        categoria_id = int(datos.get("categoria_id") or 0)
    except (TypeError, ValueError):
        raise ValidationError("Selecciona una marca y una categoría válidas.")

    if marca_id <= 0 or not _existe("marcas", marca_id):
        raise ValidationError("Selecciona una marca válida.")
    if categoria_id <= 0 or not _existe("categorias", categoria_id):
        raise ValidationError("Selecciona una categoría válida.")

    codigo = (datos.get("codigo") or "").strip().upper()
    if not codigo:
        codigo = _siguiente_codigo()

    return {
        "codigo": codigo,
        "nombre": nombre,
        "descripcion": (datos.get("descripcion") or "").strip(),
        "tipo_producto": tipo,
        "marca_id": marca_id,
        "categoria_id": categoria_id,
        "modelo": (datos.get("modelo") or "").strip().upper(),
        "precio_compra": compra,
        "precio_venta": venta,
        "precio_promocional": promo,
        "unidad_medida": unidad,
        "activo": datos.get("activo") == "on",
    }


def listar_productos() -> list:
    """Devuelve todos los productos (activos e inactivos) con marca y categoría."""
    try:
        resp = get_reader().table("productos").select(SELECT_PRODUCTO).order("id").execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    return resp.data or []


def obtener_producto(producto_id: int) -> dict:
    try:
        resp = (
            get_reader()
            .table("productos")
            .select(SELECT_PRODUCTO)
            .eq("id", producto_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    if not resp.data:
        raise NotFoundError("El producto no existe o fue eliminado.")
    return resp.data[0]


def listar_marcas() -> list:
    try:
        resp = get_reader().table("marcas").select("id,nombre").order("nombre").execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    return resp.data or []


def listar_categorias() -> list:
    try:
        resp = get_reader().table("categorias").select("id,nombre").order("nombre").execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    return resp.data or []


def crear_producto(datos: dict) -> dict:
    fila = _validar_y_mapear(datos)
    try:
        resp = get_admin_client().table("productos").insert(fila).execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    return resp.data[0]


def actualizar_producto(producto_id: int, datos: dict) -> dict:
    if not _existe("productos", producto_id):
        raise NotFoundError("El producto no existe o fue eliminado.")
    fila = _validar_y_mapear(datos)
    try:
        resp = (
            get_admin_client()
            .table("productos")
            .update(fila)
            .eq("id", producto_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    return resp.data[0] if resp.data else {}


def eliminar_producto(producto_id: int) -> None:
    if not _existe("productos", producto_id):
        raise NotFoundError("El producto no existe o fue eliminado.")
    try:
        get_admin_client().table("productos").delete().eq("id", producto_id).execute()
    except Exception as exc:  # noqa: BLE001
        msg = error_postgrest(exc) or str(exc)
        if "23503" in (getattr(exc, "code", "") or "") or "constraint" in msg.lower():
            raise ValidationError(
                "No se puede eliminar: el producto está referenciado en "
                "ventas o reparaciones. Puedes desactivarlo en edición."
            )
        raise ValidationError(msg) from exc