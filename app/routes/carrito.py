"""Carrito persistente en la base de datos (tablas carritos/detalle_carrito).

Cada usuario (o sesión anónima) tiene su carrito guardado en Supabase desde el
primer "Agregar". Los carritos vencen automáticamente (CART_TTL_HOURS) y se
avisa al usuario con mensajes flash.
"""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.exceptions import AppError, ValidationError
from app.services import carrito_service

bp = Blueprint("carrito", __name__)


def _usuario_sesion() -> str:
    """Clave de carrito: asociada al usuario si está logueado, si no, anónima."""
    if session.get("usuario_id"):
        return f"user:{session['usuario_id']}"
    token = session.get("carrito_sesion")
    if not token:
        token = carrito_service.nueva_sesion()
        session["carrito_sesion"] = token
    return f"anon:{token}"


def _preparar_carrito() -> int:
    """Devuelve el carrito vigente en BD, avisando expiración/creación con flash."""
    usuario_sesion = _usuario_sesion()
    carrito_id, es_nuevo, hubo_expiracion = carrito_service.obtener_carrito_vigente(
        usuario_sesion
    )
    session["carrito_id"] = carrito_id

    if hubo_expiracion:
        flash(
            "El carrito anterior venció (vigencia de {} horas) y se vació "
            "automáticamente. Te preparamos un carrito nuevo.".format(
                carrito_service.HORAS_VIGENCIA
            ),
            "warning",
        )
    if es_nuevo:
        flash(
            "Tu carrito se guarda en la base de datos y esperará por {} horas "
            "desde su creación; luego se elimina solo.".format(
                carrito_service.HORAS_VIGENCIA
            ),
            "info",
        )

    # Migra un carrito antiguo de la sesión (formato {id: cantidad}) a la BD.
    legacy = session.get("carrito") or {}
    if legacy and session.get("carrito_id"):
        for pid, qty in legacy.items():
            try:
                carrito_service.agregar(carrito_id, int(pid), int(qty))
            except Exception:  # noqa: BLE001
                continue
        session.pop("carrito", None)

    session["carrito_n"] = carrito_service.contar(carrito_id)
    return carrito_id


@bp.get("/carrito")
def ver():
    carrito_id = _preparar_carrito()
    items = carrito_service.listar(carrito_id)
    subtotal = sum(i["subtotal_num"] for i in items)
    igv = round(subtotal * carrito_service.IGV_TASA, 2)
    total = round(subtotal + igv, 2)
    return render_template(
        "carrito.html",
        items=items,
        subtotal=f"S/ {subtotal:,.2f}",
        igv=f"S/ {igv:,.2f}",
        total=f"S/ {total:,.2f}",
        n_items=session.get("carrito_n", 0),
        vigencia_horas=carrito_service.HORAS_VIGENCIA,
    )


@bp.post("/carrito/agregar/<int:producto_id>")
def agregar(producto_id):
    cantidad = request.form.get("cantidad", 1, type=int)
    if cantidad < 1:
        cantidad = 1

    carrito_id = _preparar_carrito()
    try:
        carrito_service.agregar(carrito_id, producto_id, cantidad)
        session["carrito_n"] = carrito_service.contar(carrito_id)
        flash("Producto agregado al carrito.", "success")
    except (AppError, ValidationError) as exc:
        flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
    except Exception:  # noqa: BLE001
        flash("No se pudo agregar el producto al carrito.", "error")
    return redirect(request.referrer or url_for("main.tienda"))


def _carrito_actual() -> int | None:
    return session.get("carrito_id")


@bp.get("/carrito/aumentar/<int:producto_id>")
def aumentar(producto_id):
    carrito_id = _carrito_actual()
    if not carrito_id:
        return redirect(url_for("carrito.ver"))
    actual = carrito_service.cantidad(carrito_id, producto_id)
    carrito_service.set_cantidad(carrito_id, producto_id, actual + 1)
    session["carrito_n"] = carrito_service.contar(carrito_id)
    return redirect(url_for("carrito.ver"))


@bp.get("/carrito/disminuir/<int:producto_id>")
def disminuir(producto_id):
    carrito_id = _carrito_actual()
    if not carrito_id:
        return redirect(url_for("carrito.ver"))
    actual = carrito_service.cantidad(carrito_id, producto_id)
    carrito_service.set_cantidad(carrito_id, producto_id, actual - 1)
    session["carrito_n"] = carrito_service.contar(carrito_id)
    return redirect(url_for("carrito.ver"))


@bp.post("/carrito/vaciar")
def vaciar():
    carrito_id = _carrito_actual()
    if carrito_id:
        carrito_service.vaciar(carrito_id)
        session["carrito_n"] = 0
    flash("Carrito vaciado.", "success")
    return redirect(url_for("carrito.ver"))


@bp.post("/carrito/finalizar")
def finalizar():
    carrito_id = _carrito_actual()
    if not carrito_id:
        flash("Tu carrito está vacío.", "error")
        return redirect(url_for("carrito.ver"))
    try:
        pedido = carrito_service.finalizar(carrito_id, session.get("usuario_id"))
    except (AppError, ValidationError) as exc:
        flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
        return redirect(url_for("carrito.ver"))
    except Exception:  # noqa: BLE001
        flash("No se pudo registrar el pedido. Inténtalo de nuevo.", "error")
        return redirect(url_for("carrito.ver"))

    session.pop("carrito_id", None)
    session.pop("carrito", None)
    session["carrito_n"] = 0
    flash(
        "Pedido {} registrado correctamente ({} producto{}). Total: {}. "
        "Los detalles se guardaron en la base de datos.".format(
            pedido["codigo_pedido"],
            pedido["n_items"],
            "s" if pedido["n_items"] != 1 else "",
            pedido["total"],
        ),
        "success",
    )
    return redirect(url_for("carrito.ver"))