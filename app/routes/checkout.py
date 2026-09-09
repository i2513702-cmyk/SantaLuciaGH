"""Checkout simulado (en memoria, sin tocar la base de datos por ahora).

El usuario elige la forma de pago (las formas se leen de la tabla `metodos_pago`),
se simula el pago en la sesión (no se inserta nada en ventas/pagos/comprobantes)
y se muestra la confirmación con su comprobante PDF. Más adelante se conectará
a la base de datos.
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
    metodo = None
    for mp in carrito_service.listar_metodos_pago():
        if mp["id"] == metodo_pago_id:
            metodo = mp
            break
    if not metodo:
        flash("Selecciona una forma de pago válida.", "error")
        return redirect(url_for("checkout.checkout"))

    tipo = (request.form.get("tipo_comprobante", "BOLETA") or "BOLETA").upper()
    if tipo not in ("BOLETA", "FACTURA"):
        tipo = "BOLETA"

    usar_cliente = request.form.get("usar_cliente") == "1"
    cliente_nombre = "Cliente anónimo (mostrador)"
    cliente_documento = "—"
    if usar_cliente:
        nombres = request.form.get("nombres", "").strip()
        apellidos = request.form.get("apellidos", "").strip()
        if nombres or apellidos:
            cliente_nombre = " ".join(p for p in (nombres, apellidos) if p)
        cliente_documento = request.form.get("numero_documento", "").strip() or "—"
        tipo_doc_id = int(request.form.get("tipo_documento_id") or 1)
    else:
        tipo_doc_id = int(request.form.get("tipo_documento_id") or 1)

    subtotal, igv, total = _totales(items)
    codigo_pedido = f"PED-{datetime.now(timezone.utc).strftime('%y%m%d%H%M%S')}"
    serie = "B001" if tipo == "BOLETA" else "F001"
    numero = str(uuid.uuid4().hex)[:8].upper()

    # Pago totalmente simulado, nada se guarda en la base de datos todavía.
    resumen = {
        "venta_id": 0,
        "codigo_pedido": codigo_pedido,
        "tipo_comprobante": tipo,
        "serie": f"{serie}-{numero}",
        "fecha_emision": datetime.now(timezone.utc).isoformat(),
        "cliente_nombre": cliente_nombre,
        "cliente_documento": cliente_documento,
        "cliente_tipo_documento_id": str(tipo_doc_id),
        "metodo_pago": metodo["nombre"],
        "transaction_id": f"SIM-{codigo_pedido}",
        "estado_pago": "PAGADO",
        "subtotal": subtotal,
        "igv": igv,
        "total": total,
        "lineas": items,
    }

    session.pop("carrito_id", None)
    session.pop("carrito", None)
    session["carrito_n"] = 0

    # Guardamos el comprobante en la sesión para permitir la descarga del PDF
    # (el pago es simulado, aún no se persiste en la base de datos).
    session["pago_simulado"] = resumen

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
