"""Panel de KPIs del administrador.

Rutas protegidas con `roles_required("admin")`, que solo admite el rol
`ADMINISTRADOR` de la base de datos (el supervisor tiene su propio panel).
El calculo vive en `app.services.kpi_service`; aqui solo se pide el periodo,
se devuelve el resultado (HTML o JSON) y se genera la descarga CSV.
"""

from datetime import datetime, timezone

from flask import (
    Blueprint,
    Response,
    jsonify,
    render_template,
    request,
    url_for,
)

from app.decorators import roles_required
from app.services import kpi_service as kpis

bp = Blueprint("kpi", __name__)

#: Icono de cada tarjeta (mismo lenguaje visual que el resto del panel).
ICONOS = {
    "KPI-01": "⏱️",
    "KPI-02": "🎯",
    "KPI-03": "🛒",
    "KPI-04": "📦",
    "KPI-05": "🧺",
    "KPI-06": "📨",
}

#: Subtitulo que explica el origen del dato de cada tarjeta.
#: Vive en el servicio para que el JSON que consume el JS lo traiga tambien.
SUBTITULOS = kpis.SUBTITULOS


def _definiciones() -> list[dict]:
    """Datos fijos de las 6 tarjetas (el panel los pinta, el JS los actualiza)."""
    salida = []
    for codigo, (nombre, unidad) in kpis.METAS.items():
        meta = kpis.meta_de(codigo)
        salida.append(
            {
                "codigo": codigo,
                "nombre": nombre,
                "unidad": unidad,
                "meta": meta,
                "meta_texto": f"{meta:g} {'h' if unidad == 'horas' else '%'}",
                "menor_es_mejor": codigo in ("KPI-01", "KPI-05"),
                "ayuda": kpis.AYUDA[codigo],
                "subtitulo": SUBTITULOS[codigo],
                "icono": ICONOS[codigo],
            }
        )
    return salida


def _periodo_desde_args():
    """Lee `desde`/`hasta` de la query string y valida el rango."""
    return kpis.parsear_periodo(
        request.args.get("desde"), request.args.get("hasta")
    )


def _contexto_extra(periodo_txt: str | None, error_periodo: str | None) -> dict:
    return {
        "kpi_periodo": periodo_txt,
        "kpi_periodo_error": error_periodo,
        "kpi_ventanas": kpis.ventanas_por_defecto(),
        "kpi_defs": _definiciones(),
    }


@bp.get("/admin/kpis")
@roles_required("admin")
def panel():
    """Pagina del modulo: 6 tarjetas, 6 graficos y filtros de fecha."""
    desde, hasta, error_periodo = _periodo_desde_args()
    ctx = _contexto_extra(f"{desde.isoformat()}|{hasta.isoformat()}", error_periodo)
    try:
        ctx["kpi"] = kpis.calcular(desde, hasta)
        ctx["kpi_error"] = None
    except kpis.KpiError as exc:
        ctx["kpi"] = None
        ctx["kpi_error"] = str(exc)
    ctx["kpis_csv_url"] = url_for(
        "kpi.csv", desde=desde.isoformat(), hasta=hasta.isoformat()
    )
    ctx["kpis_json_url"] = url_for("kpi.datos")
    return render_template("admin/kpis.html", **ctx)


@bp.get("/admin/kpis/datos")
@roles_required("admin")
def datos():
    """Mismo calculo en JSON, para que los filtros no recarguen la pagina."""
    desde, hasta, error_periodo = _periodo_desde_args()
    if error_periodo:
        return jsonify({"ok": False, "error": error_periodo}), 400
    try:
        return jsonify({"ok": True, "kpi": kpis.calcular(desde, hasta)})
    except kpis.KpiError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@bp.get("/admin/kpis/csv")
@roles_required("admin")
def csv():
    """Descarga el resumen del periodo en CSV (separador ';' para Excel)."""
    desde, hasta, error_periodo = _periodo_desde_args()
    if error_periodo:
        return jsonify({"ok": False, "error": error_periodo}), 400
    try:
        resumen = kpis.calcular(desde, hasta)
        contenido = kpis.csv(resumen)
    except kpis.KpiError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    sello = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    nombre = f"kpis-santa-lucia-{desde.isoformat()}_{hasta.isoformat()}-{sello}.csv"
    # BOM para que Excel respete los acentos.
    return Response(
        "﻿" + contenido,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
