"""Reportes del panel administrativo (ventas, productos, inventario y citas).

Cada reporte es una funcion `(desde, hasta) -> dict` que lee Supabase con el
cliente REST y devuelve SIEMPRE la misma estructura:

    {
        "titulo":          str,                       # incluye el periodo si aplica
        "columnas":        list[str],
        "filas":           list[list[str]],           # celdas ya formateadas
        "resumen":         list[{"etiqueta", "valor"}],
        "alinear_derecha": list[int],                 # indices de columnas numericas
    }

Pantalla, CSV y PDF salen de ese mismo dato (`csv_reporte`, `pdf_reporte`).

Para agregar un reporte nuevo basta con escribir su funcion y sumarla al
diccionario `REPORTES` (al final del archivo).

No se crean tablas, vistas ni funciones SQL: todo se arma con lo que ya existe
(`ventas`, `detalle_venta`, `productos`, `inventario`, `reservas`, `tecnicos`,
`empleados`).

Supuestos de negocio (ver tambien el README de la entrega):
  * Una venta ANULADA / CANCELADA / RECHAZADA no suma al "total vendido" ni a
    los productos mas vendidos, pero si aparece en la tabla y en "pedidos por
    estado" para que se vea el panorama completo.
  * `reservas.fecha_reserva` es la fecha local de la cita; se filtra por fecha
    sin conversion de zona horaria.
  * El inventario muestra solo productos activos (igual que KPI-04).
"""

from __future__ import annotations

import csv as _csv
import io
import logging
from collections import Counter
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

from app.services.kpi_service import (  # reutilizados tal cual (hora de Peru)
    LIMA,
    LOTE_IDS,
    PAGINA,
    _desde_ts,
    _dt,
    _hasta_ts,
    parsear_periodo,
    ventanas_por_defecto,
)
from app.supabase_client import error_postgrest, get_reader

log = logging.getLogger(__name__)

__all__ = [
    "REPORTES",
    "UMBRAL_STOCK_BAJO",
    "ReporteError",
    "generar",
    "csv_reporte",
    "pdf_reporte",
    "parsear_periodo",
    "ventanas_por_defecto",
]

#: Stock <= este valor (y > 0) se considera "Stock bajo". Cambiar aqui.
UMBRAL_STOCK_BAJO = 5

SIN_DATOS = "Sin datos en este periodo"


class ReporteError(Exception):
    """No se pudo leer Supabase para armar el reporte."""


# ---------------------------------------------------------------------------
# Lectura desde Supabase
# ---------------------------------------------------------------------------

def _leer(tabla: str, select: str, filtro=None, orden=(("id", False),)) -> list[dict]:
    """Lee una tabla completa paginando de a PAGINA filas (limite de PostgREST).

    `filtro` es un callable `builder -> builder` que se aplica dentro del
    try, asi un Supabase mal configurado tambien termina en ReporteError.
    `orden` es una lista de (columna, descendente) para que la paginacion sea
    estable entre peticiones.
    """
    try:
        q = get_reader().table(tabla).select(select)
        if filtro is not None:
            q = filtro(q)
        for col, desc in orden:
            q = q.order(col, desc=desc)
        filas: list[dict] = []
        inicio = 0
        while True:
            lote = q.range(inicio, inicio + PAGINA - 1).execute().data or []
            filas.extend(lote)
            if len(lote) < PAGINA:
                return filas
            inicio += PAGINA
    except Exception as exc:  # noqa: BLE001
        log.exception("Reportes: fallo al leer %s", tabla)
        raise ReporteError(
            f"No se pudo leer «{tabla}» en Supabase: {error_postgrest(exc)}"
        ) from exc


def _leer_por_ids(tabla: str, select: str, columna: str, ids: list, orden=(("id", False),)) -> list[dict]:
    """Lee filas cuyo `columna` esta en `ids`, en lotes de LOTE_IDS (.in_())."""
    filas: list[dict] = []
    for i in range(0, len(ids), LOTE_IDS):
        lote = ids[i : i + LOTE_IDS]
        filas.extend(
            _leer(tabla, select, filtro=lambda q, lote=lote: q.in_(columna, lote), orden=orden)
        )
    return filas


# ---------------------------------------------------------------------------
# Formato de celdas
# ---------------------------------------------------------------------------

def _soles(valor) -> str:
    try:
        return f"S/ {float(valor or 0):,.2f}"
    except (TypeError, ValueError):
        return "S/ 0.00"


def _numero(valor) -> str:
    """Entero si no tiene decimales, si no hasta 2 decimales."""
    try:
        n = float(valor or 0)
    except (TypeError, ValueError):
        return "0"
    return f"{int(n):,}" if n == int(n) else f"{n:,.2f}"


def _fecha_hora_lima(valor) -> str:
    """Timestamp UTC de PostgREST -> 'dd/mm/aaaa HH:MM' en hora de Peru."""
    d = _dt(valor)
    return d.astimezone(LIMA).strftime("%d/%m/%Y %H:%M") if d else "—"


def _fecha_local(valor) -> str:
    """Fecha local (columna date) -> 'dd/mm/aaaa', sin convertir zona horaria."""
    try:
        return date.fromisoformat(str(valor)[:10]).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return "—"


def _rotulo(valor, defecto: str = "—") -> str:
    """'PAGO_CONFIRMADO' -> 'Pago confirmado'."""
    texto = str(valor or "").strip().replace("_", " ")
    return texto.capitalize() if texto else defecto


def _periodo_txt(desde: date, hasta: date) -> str:
    return f"{desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"


def _por_estado(estados) -> list[dict]:
    """Tarjetas 'estado: cantidad', ordenadas de mayor a menor."""
    cuenta = Counter(_rotulo(e, "Sin estado") for e in estados)
    return [
        {"etiqueta": est, "valor": str(n)}
        for est, n in sorted(cuenta.items(), key=lambda x: (-x[1], x[0]))
    ]


def _cuenta_como_venta(estado) -> bool:
    e = (estado or "").strip().upper()
    return not any(m in e for m in ("CANCEL", "ANUL", "RECHAZ"))


# ---------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------

def _ventas_del_periodo(desde: date, hasta: date) -> list[dict]:
    return _leer(
        "ventas",
        "id,codigo_pedido,fecha_venta,estado,canal_venta,total",
        filtro=lambda q: q.gte("fecha_venta", _desde_ts(desde)).lt("fecha_venta", _hasta_ts(hasta)),
        orden=(("fecha_venta", True), ("id", False)),
    )


def reporte_ventas(desde: date, hasta: date) -> dict:
    ventas = _ventas_del_periodo(desde, hasta)
    validas = [v for v in ventas if _cuenta_como_venta(v.get("estado"))]
    total = sum(float(v.get("total") or 0) for v in validas)
    ticket = total / len(validas) if validas else 0.0

    filas = [
        [
            v.get("codigo_pedido") or f"#{v.get('id')}",
            _fecha_hora_lima(v.get("fecha_venta")),
            _rotulo(v.get("estado")),
            _rotulo(v.get("canal_venta")),
            _soles(v.get("total")),
        ]
        for v in ventas
    ]
    return {
        "titulo": f"Reporte de ventas del {_periodo_txt(desde, hasta)}",
        "columnas": ["Pedido", "Fecha", "Estado", "Canal", "Total"],
        "filas": filas,
        "resumen": [
            {"etiqueta": "Total vendido", "valor": _soles(total)},
            {"etiqueta": "Pedidos", "valor": str(len(ventas))},
            {"etiqueta": "Ticket promedio", "valor": _soles(ticket)},
            *[
                {"etiqueta": f"Pedidos · {r['etiqueta']}", "valor": r["valor"]}
                for r in _por_estado(v.get("estado") for v in ventas)
            ],
        ],
        "alinear_derecha": [4],
    }


def reporte_productos(desde: date, hasta: date) -> dict:
    ventas = _ventas_del_periodo(desde, hasta)
    ids = [v["id"] for v in ventas if _cuenta_como_venta(v.get("estado")) and v.get("id") is not None]
    lineas = _leer_por_ids("detalle_venta", "id,producto_id,cantidad,subtotal", "venta_id", ids)

    acumulado: dict = {}
    for ln in lineas:
        pid = ln.get("producto_id")
        if pid is None:
            continue
        a = acumulado.setdefault(pid, {"unidades": 0.0, "ingresos": 0.0})
        a["unidades"] += float(ln.get("cantidad") or 0)
        a["ingresos"] += float(ln.get("subtotal") or 0)

    nombres = {
        p["id"]: p.get("nombre")
        for p in _leer_por_ids("productos", "id,nombre", "id", list(acumulado))
    }
    ranking = sorted(
        (
            (nombres.get(pid) or f"Producto #{pid}", a["unidades"], a["ingresos"])
            for pid, a in acumulado.items()
        ),
        key=lambda x: (-x[1], -x[2], x[0].lower()),
    )
    filas = [
        [str(i), nombre, _numero(unid), _soles(ing)]
        for i, (nombre, unid, ing) in enumerate(ranking, start=1)
    ]
    return {
        "titulo": f"Productos más vendidos del {_periodo_txt(desde, hasta)}",
        "columnas": ["N.º", "Producto", "Unidades vendidas", "Ingresos"],
        "filas": filas,
        "resumen": [
            {"etiqueta": "Productos distintos", "valor": str(len(ranking))},
            {"etiqueta": "Unidades vendidas", "valor": _numero(sum(r[1] for r in ranking))},
            {"etiqueta": "Ingresos", "valor": _soles(sum(r[2] for r in ranking))},
        ],
        "alinear_derecha": [0, 2, 3],
    }


def _estado_stock(stock: float) -> str:
    if stock <= 0:
        return "Sin stock"
    if stock <= UMBRAL_STOCK_BAJO:
        return "Stock bajo"
    return "Disponible"


def reporte_inventario(desde: date | None = None, hasta: date | None = None) -> dict:
    """Foto actual del inventario. No usa periodo (los argumentos se ignoran)."""
    productos = _leer(
        "productos",
        "id,codigo,nombre,activo",
        filtro=lambda q: q.eq("activo", True),
    )
    # Orden por producto_id: es la unica columna de `inventario` que el resto
    # del proyecto da por segura (no se asume que exista `id`).
    inventario = _leer(
        "inventario", "producto_id,stock_actual", orden=(("producto_id", False),)
    )
    stock: dict = {}
    for f in inventario:  # varias filas por producto -> se suman
        pid = f.get("producto_id")
        if pid is None:
            continue
        try:
            stock[pid] = stock.get(pid, 0.0) + float(f.get("stock_actual") or 0)
        except (TypeError, ValueError):
            stock.setdefault(pid, 0.0)

    orden_estado = {"Sin stock": 0, "Stock bajo": 1, "Disponible": 2}
    items = []
    for p in productos:
        s = float(stock.get(p["id"], 0.0))
        items.append((p.get("codigo") or "—", p.get("nombre") or "(sin nombre)", s, _estado_stock(s)))
    items.sort(key=lambda x: (orden_estado[x[3]], x[2], x[1].lower()))

    ahora = datetime.now(LIMA).strftime("%d/%m/%Y %H:%M")
    cuenta = Counter(i[3] for i in items)
    return {
        "titulo": f"Inventario al {ahora}",
        "columnas": ["Código", "Producto", "Stock actual", "Estado"],
        "filas": [[c, n, _numero(s), e] for c, n, s, e in items],
        "resumen": [
            {"etiqueta": "Productos activos", "valor": str(len(items))},
            {"etiqueta": "Sin stock", "valor": str(cuenta["Sin stock"])},
            {"etiqueta": f"Stock bajo (≤ {UMBRAL_STOCK_BAJO})", "valor": str(cuenta["Stock bajo"])},
            {"etiqueta": "Disponible", "valor": str(cuenta["Disponible"])},
        ],
        "alinear_derecha": [2],
    }


def _tecnico_txt(reserva: dict) -> str:
    t = reserva.get("tecnicos")
    if isinstance(t, list):  # PostgREST puede devolver lista segun la relacion
        t = t[0] if t else None
    emp = (t or {}).get("empleados")
    if isinstance(emp, list):
        emp = emp[0] if emp else None
    nombre = " ".join(
        x for x in ((emp or {}).get("nombres"), (emp or {}).get("apellidos")) if x
    ).strip()
    return nombre or "Sin asignar"


def reporte_citas(desde: date, hasta: date) -> dict:
    # fecha_reserva se compara por fecha (hasta exclusivo = dia siguiente).
    reservas = _leer(
        "reservas",
        "id,fecha_reserva,hora_inicio,estado,tipo_atencion,tecnicos(id,empleados(nombres,apellidos))",
        filtro=lambda q: q.gte("fecha_reserva", desde.isoformat()).lt(
            "fecha_reserva", (hasta + timedelta(days=1)).isoformat()
        ),
        orden=(("fecha_reserva", False), ("hora_inicio", False), ("id", False)),
    )
    filas = [
        [
            _fecha_local(r.get("fecha_reserva")),
            str(r.get("hora_inicio") or "—")[:5],
            _rotulo(r.get("estado")),
            _rotulo(r.get("tipo_atencion")),
            _tecnico_txt(r),
        ]
        for r in reservas
    ]
    return {
        "titulo": f"Reporte de citas del {_periodo_txt(desde, hasta)}",
        "columnas": ["Fecha", "Hora", "Estado", "Tipo de atención", "Técnico"],
        "filas": filas,
        "resumen": [
            {"etiqueta": "Citas", "valor": str(len(reservas))},
            *[
                {"etiqueta": f"Citas · {r['etiqueta']}", "valor": r["valor"]}
                for r in _por_estado(r.get("estado") for r in reservas)
            ],
        ],
        "alinear_derecha": [],
    }


#: Para agregar un reporte: escribir su funcion y sumarla aqui.
#: `usa_periodo=False` oculta el filtro Desde/Hasta (reportes "foto actual").
REPORTES: dict[str, dict] = {
    "ventas": {"nombre": "Ventas", "icono": "receipt", "usa_periodo": True, "funcion": reporte_ventas},
    "productos": {"nombre": "Productos más vendidos", "icono": "trophy", "usa_periodo": True, "funcion": reporte_productos},
    "inventario": {"nombre": "Inventario", "icono": "box-seam", "usa_periodo": False, "funcion": reporte_inventario},
    "citas": {"nombre": "Citas", "icono": "calendar-check", "usa_periodo": True, "funcion": reporte_citas},
}


def generar(tipo: str, desde: date, hasta: date) -> dict:
    """Ejecuta el reporte `tipo`. KeyError si no existe (la ruta ya da 404)."""
    return REPORTES[tipo]["funcion"](desde, hasta)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _csv_seguro(celda) -> str:
    """Evita inyeccion de formulas en Excel (=, +, -, @ al inicio)."""
    texto = str(celda)
    return "'" + texto if texto[:1] in ("=", "+", "-", "@", "\t", "\r") else texto


def csv_reporte(rep: dict) -> str:
    """CSV con separador ';' (Excel es-PE). El BOM lo agrega la ruta, como en KPIs."""
    buf = io.StringIO()
    w = _csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow([_csv_seguro(rep["titulo"])])
    w.writerow([])
    for r in rep["resumen"]:
        w.writerow([_csv_seguro(r["etiqueta"]), _csv_seguro(r["valor"])])
    w.writerow([])
    w.writerow([_csv_seguro(c) for c in rep["columnas"]])
    if rep["filas"]:
        for fila in rep["filas"]:
            w.writerow([_csv_seguro(c) for c in fila])
    else:
        w.writerow([SIN_DATOS])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF (reportlab, mismo enfoque que pdf_service)
# ---------------------------------------------------------------------------

def pdf_reporte(rep: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    columnas, filas = rep["columnas"], rep["filas"]
    derecha = set(rep.get("alinear_derecha") or [])
    pagina = landscape(A4) if len(columnas) > 5 else A4
    margen = 15 * mm
    ancho = pagina[0] - 2 * margen

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=pagina, leftMargin=margen, rightMargin=margen,
        topMargin=14 * mm, bottomMargin=16 * mm, title=rep["titulo"],
    )
    base = getSampleStyleSheet()["Normal"]
    marca = ParagraphStyle("marca", parent=base, fontSize=9, textColor=colors.grey, alignment=TA_CENTER)
    titulo = ParagraphStyle("titulo", parent=base, fontName="Helvetica-Bold", fontSize=14, spaceAfter=6)
    celda = ParagraphStyle("celda", parent=base, fontSize=8.5, leading=10.5)
    celda_d = ParagraphStyle("celda_d", parent=celda, alignment=TA_RIGHT)
    cab = ParagraphStyle("cab", parent=celda, fontName="Helvetica-Bold", textColor=colors.white)
    cab_d = ParagraphStyle("cab_d", parent=cab, alignment=TA_RIGHT)
    r_et = ParagraphStyle("r_et", parent=base, fontSize=8, textColor=colors.grey)
    r_val = ParagraphStyle("r_val", parent=base, fontName="Helvetica-Bold", fontSize=12)

    def p(texto, estilo):
        return Paragraph(escape(str(texto)), estilo)

    historia = [
        p("Santa Lucía · Servicio Técnico", marca),
        Spacer(1, 3),
        p(rep["titulo"], titulo),
    ]

    # --- Tarjetas de resumen (rejilla de hasta 4 por fila) ---
    resumen = rep["resumen"]
    if resumen:
        por_fila = 4
        rejilla = []
        for i in range(0, len(resumen), por_fila):
            tramo = resumen[i : i + por_fila]
            tramo += [{"etiqueta": "", "valor": ""}] * (por_fila - len(tramo))
            rejilla.append([[p(r["etiqueta"], r_et), p(r["valor"], r_val)] for r in tramo])
        t_res = Table(rejilla, colWidths=[ancho / por_fila] * por_fila)
        t_res.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.4, colors.lightgrey),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f8fb")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        historia += [t_res, Spacer(1, 10)]

    # --- Tabla de datos ---
    if filas:
        # Ancho proporcional al texto mas largo de cada columna (acotado).
        pesos = []
        for j, c in enumerate(columnas):
            largo = max([len(str(c))] + [len(str(f[j])) for f in filas[:200]])
            pesos.append(min(max(largo, 6), 40))
        anchos = [ancho * w / sum(pesos) for w in pesos]
        datos = [[p(c, cab_d if j in derecha else cab) for j, c in enumerate(columnas)]]
        datos += [
            [p(v, celda_d if j in derecha else celda) for j, v in enumerate(f)] for f in filas
        ]
        t = Table(datos, colWidths=anchos, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6efd")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fb")]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        historia.append(t)
    else:
        historia.append(p(SIN_DATOS, ParagraphStyle("vacio", parent=base, alignment=TA_CENTER, textColor=colors.grey)))

    generado = datetime.now(LIMA).strftime("%d/%m/%Y %H:%M")

    def pie(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(margen, 9 * mm, f"Generado el {generado} (hora de Perú)")
        canvas.drawRightString(pagina[0] - margen, 9 * mm, f"Página {d.page}")
        canvas.restoreState()

    doc.build(historia, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()
