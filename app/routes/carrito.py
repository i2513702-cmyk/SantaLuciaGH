"""Carrito de compras básico: guardado en la sesión del servidor.

Opera sobre la tabla `productos` de Supabase (precios reales de la base).
"""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.supabase_client import get_reader

bp = Blueprint("carrito", __name__)

MAX_CANTIDAD = 99


def _precio(valor):
    try:
        return f"S/ {float(valor):,.2f}"
    except (TypeError, ValueError):
        return "—"


@bp.get("/carrito")
def ver():
    carrito = session.get("carrito", {}) or {}
    ids = [int(pid) for pid, qty in carrito.items() if int(qty) > 0]

    items = []
    total = 0.0
    if ids:
        try:
            resp = (
                get_reader()
                .table("productos")
                .select(
                    "id,nombre,precio_venta,precio_promocional,"
                    "marcas(nombre),categorias(nombre)"
                )
                .in_("id", ids)
                .execute()
            )
            productos = {p["id"]: p for p in resp.data or []}
        except Exception:  # noqa: BLE001
            productos = {}

        for pid in ids:
            p = productos.get(pid)
            if not p:
                continue
            qty = int(carrito[str(pid)])
            precio = p.get("precio_promocional") or p.get("precio_venta") or 0
            subtotal = float(precio) * qty
            total += subtotal
            items.append({
                "id": pid,
                "nombre": p.get("nombre"),
                "marca": (p.get("marcas") or {}).get("nombre"),
                "precio_unitario": _precio(precio),
                "precio_num": float(precio),
                "cantidad": qty,
                "subtotal": _precio(subtotal),
            })

    return render_template(
        "carrito.html",
        items=items,
        total=_precio(total),
        n_items=sum(int(q) for q in carrito.values()),
    )


def _cant(producto_id: int) -> int:
    carrito = session.get("carrito", {}) or {}
    return int(carrito.get(str(producto_id), 0))


@bp.post("/carrito/agregar/<int:producto_id>")
def agregar(producto_id):
    cantidad = request.form.get("cantidad", 1, type=int)
    if cantidad < 1:
        cantidad = 1

    carrito = session.get("carrito", {}) or {}
    actual = int(carrito.get(str(producto_id), 0))
    carrito[str(producto_id)] = min(actual + cantidad, MAX_CANTIDAD)
    session["carrito"] = carrito
    flash("Producto agregado al carrito.", "success")
    return redirect(request.referrer or url_for("main.tienda"))


@bp.get("/carrito/aumentar/<int:producto_id>")
def aumentar(producto_id):
    carrito = session.get("carrito", {}) or {}
    carrito[str(producto_id)] = min(_cant(producto_id) + 1, MAX_CANTIDAD)
    session["carrito"] = carrito
    return redirect(url_for("carrito.ver"))


@bp.get("/carrito/disminuir/<int:producto_id>")
def disminuir(producto_id):
    carrito = session.get("carrito", {}) or {}
    nueva = _cant(producto_id) - 1
    if nueva > 0:
        carrito[str(producto_id)] = nueva
    else:
        carrito.pop(str(producto_id), None)
    session["carrito"] = carrito
    return redirect(url_for("carrito.ver"))


@bp.post("/carrito/vaciar")
def vaciar():
    session.pop("carrito", None)
    flash("Carrito vaciado.", "success")
    return redirect(url_for("carrito.ver"))