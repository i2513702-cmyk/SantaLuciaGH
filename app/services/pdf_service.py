"""Genera un comprobante PDF tipo boleta/factura electrónica con reportlab."""

from datetime import datetime, timezone
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER


def _fmt_fecha(valor: str) -> str:
    try:
        d = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        if d.tzinfo is not None:
            d = d.astimezone(timezone.utc)
        return d.strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return str(valor or "")


def _dinero(valor) -> str:
    try:
        return f"S/ {float(valor or 0):,.2f}"
    except (TypeError, ValueError):
        return "S/ 0.00"


def generar_comprobante_pdf(resumen: dict) -> bytes:
    """Devuelve el PDF (bytes) de una boleta/factura a partir del resumen."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Comprobante {resumen['tipo_comprobante']}",
    )

    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle(
        "Titulo",
        parent=estilos["Title"],
        fontSize=16,
        alignment=TA_CENTER,
        spaceAfter=2,
    )
    subt = ParagraphStyle(
        "Subt",
        parent=estilos["Normal"],
        alignment=TA_CENTER,
        textColor=colors.grey,
        spaceAfter=8,
    )
    ex = ParagraphStyle("Ex", parent=estilos["Normal"], fontSize=9, textColor=colors.grey)
    normal = estilos["Normal"]

    historial = []
    historial.append(Paragraph("Santa Lucia", titulo))
    historial.append(Paragraph("Todo para tu hogar y más", subt))
    historial.append(Paragraph(
        "COMPROBANTE ELECTRÓNICO (DEMO) — Pago simulado",
        ParagraphStyle("demo", parent=subt, textColor=colors.HexColor("#0d6efd")),
    ))
    historial.append(Spacer(1, 4))

    cab = Table(
        [
            ["Comprobante:", f"{resumen['tipo_comprobante']} {resumen['serie']}"],
            ["Pedido:", resumen["codigo_pedido"]],
            ["Fecha de emisión:", _fmt_fecha(resumen.get("fecha_emision"))],
            ["Cliente:", resumen["cliente_nombre"]],
            ["Documento:", resumen["cliente_documento"]],
            ["Forma de pago:", resumen["metodo_pago"]],
            ["Transacción:", resumen["transaction_id"] or "—"],
        ],
        colWidths=[50 * mm, 110 * mm],
    )
    cab.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    historial.append(cab)
    historial.append(Spacer(1, 10))

    items = resumen.get("lineas") or resumen.get("items") or []

    filas = [["#", "Producto", "Cantidad", "Precio unitario", "Subtotal"]]
    for idx, item in enumerate(items, start=1):
        filas.append([
            str(idx),
            item["nombre"],
            str(item["cantidad"]),
            _dinero(item["precio_num"]),
            _dinero(item["subtotal_num"]),
        ])

    tabla = Table(filas, colWidths=[10 * mm, 86 * mm, 20 * mm, 36 * mm, 38 * mm])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6efd")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (1, 1), (1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
    ]))
    historial.append(tabla)
    historial.append(Spacer(1, 12))

    totales = Table(
        [
            ["Subtotal:", _dinero(resumen["subtotal"])],
            ["IGV (18%):", _dinero(resumen["igv"])],
            ["TOTAL:", _dinero(resumen["total"])],
        ],
        colWidths=[58 * mm, 62 * mm],
        hAlign="RIGHT",
    )
    totales.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 2), (1, 2), "Helvetica-Bold"),
        ("FONTSIZE", (0, 2), (1, 2), 12),
        ("TEXTCOLOR", (0, 2), (1, 2), colors.HexColor("#0d6efd")),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE", (0, 2), (-1, 2), 0.6, colors.HexColor("#dee2e6")),
    ]))
    historial.append(totales)

    historial.append(Spacer(1, 16))
    historial.append(Paragraph(
        "Gracias por tu compra. Este es un comprobante generado por el sistema "
        "en modo demostración (pago simulado, sin valor fiscal real).",
        ex,
    ))

    doc.build(historial)
    return buf.getvalue()
