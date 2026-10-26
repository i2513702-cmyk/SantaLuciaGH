"""Reportes del administrador (ventas, productos, inventario y citas).

Rutas protegidas con `roles_required("admin")` (solo el rol ADMINISTRADOR).
Toda la logica vive en `app.services.reporte_service`; aqui solo se pide el
periodo, se llama al servicio y se devuelve HTML, CSV o PDF.
"""

from datetime import datetime, timezone

from flask import Blueprint, Response, abort, jsonify, render_template, request, url_for

from app.decorators import roles_required
from app.services import reporte_service as reportes

bp = Blueprint("reportes", __name__)

TIPO_POR_DEFECTO = "ventas"


def _tipo_valido(tipo: str) -> dict:
    """Definicion del reporte, o 404 si el tipo no existe."""
    if tipo not in reportes.REPORTES:
        abort(404)
    return reportes.REPORTES[tipo]


def _periodo(definicion: dict):
    """Lee `desde`/`hasta` y los valida. Los reportes sin periodo lo ignoran."""
    if not definicion["usa_periodo"]:
        desde, hasta, _ = reportes.parsear_periodo(None, None)
        return desde, hasta, None
    return reportes.parsear_periodo(request.args.get("desde"), request.args.get("hasta"))


def _error_json(mensaje: str, codigo: int):
    return jsonify({"ok": False, "error": mensaje}), codigo


def _nombre_archivo(tipo: str, definicion: dict, desde, hasta, extension: str) -> str:
    sello = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    rango = f"-{desde.isoformat()}_{hasta.isoformat()}" if definicion["usa_periodo"] else ""
    return f"reporte-{tipo}{rango}-{sello}.{extension}"


def _pantalla(tipo: str):
    definicion = _tipo_valido(tipo)
    desde, hasta, error_periodo = _periodo(definicion)

    rep, error = None, None
    try:
        rep = reportes.generar(tipo, desde, hasta)
    except reportes.ReporteError as exc:
        error = str(exc)

    rango = {"desde": desde.isoformat(), "hasta": hasta.isoformat()} if definicion["usa_periodo"] else {}
    return render_template(
        "admin/reportes.html",
        tipo=tipo,
        definicion=definicion,
        reportes=reportes.REPORTES,
        rep=rep,
        error=error,
        error_periodo=error_periodo,
        desde=desde.isoformat(),
        hasta=hasta.isoformat(),
        ventanas=reportes.ventanas_por_defecto(),
        csv_url=url_for("reportes.csv", tipo=tipo, **rango),
        pdf_url=url_for("reportes.pdf", tipo=tipo, **rango),
        umbral_stock_bajo=reportes.UMBRAL_STOCK_BAJO,
    )


@bp.get("/admin/reportes")
@roles_required("admin")
def indice():
    """Entrada del modulo: abre el reporte de ventas."""
    return _pantalla(TIPO_POR_DEFECTO)


@bp.get("/admin/reportes/<tipo>")
@roles_required("admin")
def ver(tipo):
    return _pantalla(tipo)


@bp.get("/admin/reportes/<tipo>/csv")
@roles_required("admin")
def csv(tipo):
    """CSV con separador ';' y BOM UTF-8 para que Excel respete los acentos."""
    definicion = _tipo_valido(tipo)
    desde, hasta, error_periodo = _periodo(definicion)
    if error_periodo:
        return _error_json(error_periodo, 400)
    try:
        contenido = reportes.csv_reporte(reportes.generar(tipo, desde, hasta))
    except reportes.ReporteError as exc:
        return _error_json(str(exc), 503)
    nombre = _nombre_archivo(tipo, definicion, desde, hasta, "csv")
    return Response(
        "\ufeff" + contenido,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@bp.get("/admin/reportes/<tipo>/pdf")
@roles_required("admin")
def pdf(tipo):
    definicion = _tipo_valido(tipo)
    desde, hasta, error_periodo = _periodo(definicion)
    if error_periodo:
        return _error_json(error_periodo, 400)
    try:
        contenido = reportes.pdf_reporte(reportes.generar(tipo, desde, hasta))
    except reportes.ReporteError as exc:
        return _error_json(str(exc), 503)
    nombre = _nombre_archivo(tipo, definicion, desde, hasta, "pdf")
    return Response(
        contenido,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
