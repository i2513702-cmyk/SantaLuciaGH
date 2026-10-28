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


def generar_orden_servicio_pdf(orden: dict) -> bytes:
    """Devuelve el PDF (bytes) de una orden de servicio de reparación.

    `orden` es un dict; todas las claves son opcionales (se muestra "—" si
    faltan): codigo, fecha_ingreso, estado, tipo_atencion, cliente_nombre,
    cliente_telefono, cliente_documento, direccion, equipo, numero_serie,
    problema, diagnostico, notas, costo_estimado, tecnico_nombre.
    """
    def dato(clave, defecto="—"):
        valor = orden.get(clave)
        return str(valor) if valor not in (None, "") else defecto

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Orden de servicio {dato('codigo', '')}".strip(),
    )

    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle(
        "TituloOS", parent=estilos["Title"], fontSize=16,
        alignment=TA_CENTER, spaceAfter=2,
    )
    subt = ParagraphStyle(
        "SubtOS", parent=estilos["Normal"], alignment=TA_CENTER,
        textColor=colors.grey, spaceAfter=8,
    )
    seccion = ParagraphStyle(
        "SeccionOS", parent=estilos["Heading3"], textColor=colors.HexColor("#0d6efd"),
        spaceBefore=8, spaceAfter=4,
    )
    texto = ParagraphStyle("TextoOS", parent=estilos["Normal"], fontSize=9, leading=12)
    pie = ParagraphStyle("PieOS", parent=estilos["Normal"], fontSize=8, textColor=colors.grey)

    def bloque(filas):
        t = Table(filas, colWidths=[45 * mm, 115 * mm])
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#e9ecef")),
        ]))
        return t

    def parrafo(valor):
        # Paragraph interpreta marcado: se escapa el texto libre del usuario.
        seguro = (str(valor or "—").replace("&", "&amp;")
                  .replace("<", "&lt;").replace(">", "&gt;"))
        return Paragraph(seguro.replace("\n", "<br/>"), texto)

    contenido = [
        Paragraph("Santa Lucia", titulo),
        Paragraph("ORDEN DE SERVICIO TÉCNICO", subt),
        bloque([
            ["Orden:", dato("codigo")],
            ["Fecha de ingreso:", _fmt_fecha(orden.get("fecha_ingreso")) if orden.get("fecha_ingreso") else "—"],
            ["Estado:", dato("estado")],
            ["Tipo de atención:", dato("tipo_atencion")],
            ["Técnico:", dato("tecnico_nombre")],
        ]),
        Paragraph("Cliente", seccion),
        bloque([
            ["Nombre:", dato("cliente_nombre")],
            ["Documento:", dato("cliente_documento")],
            ["Teléfono:", dato("cliente_telefono")],
            ["Dirección:", dato("direccion")],
        ]),
        Paragraph("Equipo", seccion),
        bloque([
            ["Equipo:", dato("equipo")],
            ["N.º de serie:", dato("numero_serie")],
        ]),
        Paragraph("Problema reportado", seccion),
        parrafo(orden.get("problema")),
        Paragraph("Diagnóstico del técnico", seccion),
        parrafo(orden.get("diagnostico")),
    ]

    if orden.get("notas"):
        contenido += [Paragraph("Notas", seccion), parrafo(orden.get("notas"))]

    if orden.get("costo_estimado") not in (None, ""):
        contenido += [
            Paragraph("Costo estimado", seccion),
            Paragraph(f"<b>{_dinero(orden.get('costo_estimado'))}</b>", texto),
        ]

    contenido += [
        Spacer(1, 30),
        Table(
            [["______________________", "______________________"],
             ["Firma del técnico", "Firma del cliente"]],
            colWidths=[80 * mm, 80 * mm],
            style=TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]),
        ),
        Spacer(1, 12),
        Paragraph("Documento interno de seguimiento. Sin valor tributario.", pie),
    ]

    doc.build(contenido)
    return buf.getvalue()