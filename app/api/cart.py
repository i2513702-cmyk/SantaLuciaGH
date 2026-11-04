"""Endpoints del carrito E09–E13 (solo clientes autenticados)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import jsonify, request

from app.api import bp
from app.api.common import (
    ApiError,
    api_auth,
    api_route,
    cuerpo,
    datetime_iso,
    dinero,
    entero,
    insert_row,
    now_utc_iso,
    producto_por_id,
    read_one,
    read_rows,
    requerir,
    stock_info,
    texto,
    update_row,
)
from app.services import carrito_service

IGV = 0.18


def _sesion() -> str:
    from app.api.common import contexto_actual

    return f"api:{contexto_actual().get('id')}"


def _carrito_id() -> int:
    carrito_id, _nuevo, _expirado = carrito_service.obtener_carrito_vigente(_sesion())
    return carrito_id


def _items_carrito(carrito_id: int) -> list[dict]:
    filas = read_rows(
        "detalle_carrito",
        "id,carrito_id,producto_id,cantidad,precio_unitario,"
        "productos(id,codigo,nombre,precio_venta,precio_promocional,imagen_url,"
        "marcas(nombre),categorias(nombre))",
        carrito_id=carrito_id,
        order="id",
    )
    if not filas:
        # Reintento sin imagen_url si la columna aún no existe.
        filas = read_rows(
            "detalle_carrito",
            "id,carrito_id,producto_id,cantidad,precio_unitario,"
            "productos(id,codigo,nombre,precio_venta,precio_promocional,"
            "marcas(nombre),categorias(nombre))",
            carrito_id=carrito_id,
            order="id",
        )
    stocks = stock_info([f.get("producto_id") for f in filas])
    salida = []
    for f in filas:
        prod = f.get("productos") or {}
        precio = dinero(prod.get("precio_promocional") or prod.get("precio_venta") or f.get("precio_unitario"))
        cantidad = int(f.get("cantidad") or 0)
        base = round(precio * cantidad, 2)
        salida.append(
            {
                "item_id": f.get("id"),
                "producto_id": f.get("producto_id"),
                "sku": prod.get("codigo") or f"PRO-{f.get('producto_id')}",
                "nombre": prod.get("nombre") or "",
                "imagen_url": prod.get("imagen_url"),
                "categoria": ((prod.get("categorias") or {}).get("nombre")) or "",
                "precio_unit": precio,
                "cantidad": cantidad,
                "subtotal": base,
                "stock_disponible": int(round(stocks.get(f.get("producto_id"), {}).get("disponible", 0))),
                "subtotal_base": base,
                "igv": round(base * IGV, 2),
                "total": round(base * (1 + IGV), 2),
            }
        )
    return salida


def _payload(carrito_id: int) -> dict:
    carrito = read_one("carritos", "*", id=carrito_id) or {}
    items = _items_carrito(carrito_id)
    base = round(sum(i["subtotal_base"] for i in items), 2)
    igv = round(base * IGV, 2)
    total = round(base + igv, 2)
    ultimo = carrito.get("fecha_actualizacion") or carrito.get("fecha_creacion")
    try:
        dt = datetime.fromisoformat(str(ultimo).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        expira = (dt + timedelta(hours=carrito_service.HORAS_VIGENCIA)).isoformat()
    except (TypeError, ValueError):
        expira = (datetime.now(timezone.utc) + timedelta(hours=carrito_service.HORAS_VIGENCIA)).isoformat()
    return {
        "id": carrito_id,
        "expira_en": expira,
        "items": items,
        "subtotal_base": base,
        "igv": igv,
        "total": total,
    }


def _detalle(carrito_id: int, item_id: int) -> dict:
    detalle = read_one("detalle_carrito", "*", id=item_id)
    if not detalle or detalle.get("carrito_id") != carrito_id:
        raise ApiError("El ítem no pertenece a tu carrito.", 404, "NO_ENCONTRADO")
    return detalle


def _validar_stock(producto_id: int, cantidad: int) -> None:
    disponible = stock_info([producto_id]).get(producto_id, {}).get("disponible", 0)
    if cantidad > disponible:
        raise ApiError(
            f"Solo hay {int(disponible)} unidades disponibles de este producto.",
            400,
            "STOCK_INSUFICIENTE",
            {"stock_disponible": int(disponible)},
        )


@bp.get("/cart")
@api_route
@api_auth("CLIENTE")
def cart_get():
    return jsonify(_payload(_carrito_id()))


@bp.post("/cart/items")
@api_route
@api_auth("CLIENTE")
def cart_add():
    data = cuerpo()
    producto_id = entero(data.get("producto_id"))
    cantidad = entero(data.get("cantidad"), 1)
    if producto_id <= 0:
        raise ApiError("El producto es obligatorio.", 400, "DATOS_INVALIDOS", {"producto_id": "obligatorio"})
    if cantidad < 1 or cantidad > 99:
        raise ApiError("La cantidad debe estar entre 1 y 99.", 400, "DATOS_INVALIDOS", {"cantidad": "rango"})
    producto = producto_por_id(producto_id)
    if not producto or producto.get("activo") is False:
        raise ApiError("El producto no existe o no está disponible.", 404, "NO_ENCONTRADO")
    carrito_id = _carrito_id()
    actual = carrito_service.cantidad(carrito_id, producto_id)
    _validar_stock(producto_id, actual + cantidad)
    try:
        carrito_service.agregar(carrito_id, producto_id, cantidad)
    except Exception as exc:  # noqa: BLE001
        mensaje = getattr(exc, "message", None) or str(exc) or "No se pudo agregar el producto."
        raise ApiError(mensaje, 400, "DATOS_INVALIDOS") from exc
    return jsonify(_payload(carrito_id)), 201


@bp.patch("/cart/items/<int:item_id>")
@api_route
@api_auth("CLIENTE")
def cart_update(item_id: int):
    data = cuerpo()
    carrito_id = _carrito_id()
    detalle = _detalle(carrito_id, item_id)
    producto_id = int(detalle.get("producto_id"))
    if "cantidad" in data and "accion" in data:
        raise ApiError("Envía cantidad o accion, no ambas.", 400, "DATOS_INVALIDOS")
    if "cantidad" in data:
        cantidad = entero(data.get("cantidad"), -1)
        if cantidad < 0:
            raise ApiError("La cantidad no puede ser negativa.", 400, "DATOS_INVALIDOS", {"cantidad": "rango"})
    elif "accion" in data:
        accion = texto(data.get("accion")).lower()
        actual = int(detalle.get("cantidad") or 0)
        if accion == "incrementar":
            cantidad = actual + 1
        elif accion == "decrementar":
            cantidad = max(0, actual - 1)
        else:
            raise ApiError("La acción debe ser incrementar o decrementar.", 400, "DATOS_INVALIDOS", {"accion": "opciones"})
    else:
        raise ApiError("Indica cantidad o accion.", 400, "DATOS_INVALIDOS")
    if cantidad > 99:
        raise ApiError("La cantidad máxima por producto es 99.", 400, "DATOS_INVALIDOS", {"cantidad": "maximo"})
    if cantidad > 0:
        _validar_stock(producto_id, cantidad)
    try:
        carrito_service.set_cantidad(carrito_id, producto_id, cantidad)
    except Exception as exc:  # noqa: BLE001
        mensaje = getattr(exc, "message", None) or str(exc) or "No se pudo actualizar el carrito."
        raise ApiError(mensaje, 400, "DATOS_INVALIDOS") from exc
    return jsonify(_payload(carrito_id))


@bp.delete("/cart/items/<int:item_id>")
@api_route
@api_auth("CLIENTE")
def cart_remove(item_id: int):
    carrito_id = _carrito_id()
    detalle = _detalle(carrito_id, item_id)
    carrito_service.set_cantidad(carrito_id, int(detalle.get("producto_id")), 0)
    return jsonify(_payload(carrito_id))


@bp.delete("/cart")
@api_route
@api_auth("CLIENTE")
def cart_clear():
    carrito_id = _carrito_id()
    carrito_service.vaciar(carrito_id)
    return "", 204
