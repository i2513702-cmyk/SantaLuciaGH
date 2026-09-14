"""Checkout simulado (el pago se registra en la base de datos como demo).

El usuario debe iniciar sesión para pagar: la venta, su detalle, el comprobante
y el pago se guardan en Supabase asociados al usuario (historial de compras), y
el carrito se vacía al confirmar el pago.
"""

import uuid
from datetime import datetime, timezone

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.decorators import login_required
from app.exceptions import AppError, ValidationError
from app.services import carrito_service

bp = Blueprint("checkout", __name__)

IGV_TASA = 0.18


def _usuario_sesion() -> str:
    if session.get("usuario_id"):
        return f"user:{session['usuario_id']}"
    token = session.get("carrito_sesion")
    if not token:
        token = carrito_service.nueva_sesion()
        session["carrito_sesion"] = token
    return f"anon:{token}"


def _totales(items) -> tuple[float, float, float]:
    subtotal = sum(i["subtotal_num"] for i in items)
    igv = round(subtotal * IGV_TASA, 2)
    total = round(subtotal + igv, 2)
    return subtotal, igv, total


@bp.get("/checkout")
@login_required
def checkout():
    carrito_id = session.get("carrito_id")
    if not carrito_id:
        flash("Agrega productos al carrito para pagar.", "error")
        return redirect(url_for("carrito.ver"))

    items = carrito_service.listar(carrito_id)
    if not items:
        flash("Tu carrito está vacío. Agrega productos para continuar.", "error")
        return redirect(url_for("carrito.ver"))

    _subtotal, igv, total = _totales(items)

    return render_template(
        "checkout.html",
        items=items,
        subtotal=f"S/ {_subtotal:,.2f}",
        igv=f"S/ {igv:,.2f}",
        total=f"S/ {total:,.2f}",
        metodos_pago=carrito_service.listar_metodos_pago(),
        vigencia_horas=carrito_service.HORAS_VIGENCIA,
    )


@bp.post("/checkout/procesar")
@login_required
def procesar():
    carrito_id = session.get("carrito_id")
    if not carrito_id:
        flash("Tu carrito está vacío.", "error")
        return redirect(url_for("carrito.ver"))

    items = carrito_service.listar(carrito_id)
    if not items:
        flash("Tu carrito está vacío. Agrega productos para continuar.", "error")
        return redirect(url_for("carrito.ver"))

    metodo_pago_id = request.form.get("metodo_pago_id", type=int)

    tipo = (request.form.get("tipo_comprobante", "BOLETA") or "BOLETA").upper()
    if tipo not in ("BOLETA", "FACTURA"):
        tipo = "BOLETA"

    usar_cliente = request.form.get("usar_cliente") == "1"
    cliente_nombre = "Cliente anónimo (mostrador)"
    cliente_documento = "—"
    tipo_doc_id = int(request.form.get("tipo_documento_id") or 1)
    if usar_cliente:
        nombres = request.form.get("nombres", "").strip()
        apellidos = request.form.get("apellidos", "").strip()
        if nombres or apellidos:
            cliente_nombre = " ".join(p for p in (nombres, apellidos) if p)
        cliente_documento = request.form.get("numero_documento", "").strip() or "—"

    try:
        resul = carrito_service.pagar(
            carrito_id=carrito_id,
            metodo_pago_id=metodo_pago_id,
            usuario_id=session.get("usuario_id"),
            tipo_comprobante=tipo,
        )
    except (AppError, ValidationError) as exc:
        flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
        return redirect(url_for("checkout.checkout"))
    except Exception:  # noqa: BLE001
        flash("No se pudo registrar el pago. Inténtalo de nuevo.", "error")
        return redirect(url_for("checkout.checkout"))

    subtotal = sum(i["subtotal_num"] for i in items)
    igv = round(subtotal * IGV_TASA, 2)
    total = round(subtotal + igv, 2)
    codigo_pedido = resul["codigo_pedido"]

    # Al pagar el carrito se vacía (detalle + carrito quedan fuera del carrito activo).
    try:
        carrito_service.vaciar(carrito_id)
    except Exception:  # noqa: BLE001
        pass
    session.pop("carrito_id", None)
    session.pop("carrito", None)
    session["carrito_n"] = 0

    # Comprobante guardado en la sesión para permitir la descarga del PDF.
    resumen = {
        "venta_id": resul["venta_id"],
        "codigo_pedido": codigo_pedido,
        "tipo_comprobante": resul["tipo_comprobante"],
        "serie": resul["serie"],
        "fecha_emision": datetime.now(timezone.utc).isoformat(),
        "cliente_nombre": cliente_nombre,
        "cliente_documento": cliente_documento,
        "cliente_tipo_documento_id": str(tipo_doc_id),
        "metodo_pago": resul["metodo_pago"],
        "transaction_id": f"SIM-{resul['venta_id']}",
        "estado_pago": "PAGADO",
        "subtotal": subtotal,
        "igv": igv,
        "total": total,
        "lineas": items,
    }
    session["pago_simulado"] = resumen

    flash(
        f"Pago simulado exitoso. Pedido {codigo_pedido} registrado en tu historial.",
        "success",
    )
    return render_template("confirmacion.html", resumen=resumen)


@bp.get("/checkout/pdf")
def pdf():
    resumen = session.get("pago_simulado")
    if not resumen:
        flash("No hay un comprobante reciente para descargar.", "error")
        return redirect(url_for("main.tienda"))

    from io import BytesIO

    from flask import Response

    from app.services.pdf_service import generar_comprobante_pdf

    datos = generar_comprobante_pdf(resumen)
    nombre = f"{resumen['tipo_comprobante']}_{resumen['serie']}.pdf"
    return Response(
        BytesIO(datos),
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
        },
    )
