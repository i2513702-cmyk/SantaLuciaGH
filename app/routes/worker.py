"""Panel del técnico/worker (datos reales vía API REST de Supabase)."""

from flask import Blueprint, render_template

from app.decorators import roles_required
from app.supabase_client import get_reader

bp = Blueprint("worker", __name__)

ESTADOS_LABEL = {
    "RECIBIDO": "Recibido",
    "DIAGNOSTICO": "Diagnóstico",
    "ESPERANDO_APROBACION": "Esperando aprobación",
    "APROBADO": "Aprobado",
    "ESPERANDO_REPUESTO": "Esperando repuesto",
    "EN_REPARACION": "En reparación",
    "REPARADO": "Reparado",
    "LISTO_ENTREGA": "Listo para entrega",
    "ENTREGADO": "Entregado",
    "NO_REPARABLE": "No reparable",
    "CANCELADO": "Cancelado",
}

EN_SERVICIO = ("RECIBIDO", "DIAGNOSTICO", "ESPERANDO_APROBACION",
               "APROBADO", "ESPERANDO_REPUESTO", "EN_REPARACION",
               "REPARADO", "LISTO_ENTREGA")


def _clase_estado(estado: str) -> str:
    if estado in ("ENTREGADO", "REPARADO", "LISTO_ENTREGA"):
        return "badge-ok"
    if estado in ("EN_REPARACION", "DIAGNOSTICO", "APROBADO"):
        return "badge-warn"
    return "badge-muted"


def _contar(tabla: str, **filtros):
    try:
        q = get_reader().table(tabla).select("*", count="exact")
        for campo, valor in filtros.items():
            q = q.eq(campo, valor)
        resp = q.limit(1).execute()
        return resp.count
    except Exception:  # noqa: BLE001
        return None


@bp.route("/worker")
@roles_required("worker")
def dashboard():
    try:
        reparaciones = (
            get_reader()
            .table("reparaciones")
            .select("*")
            .order("fecha_ingreso", desc=True)
            .limit(8)
            .execute()
        ).data or []
    except Exception:  # noqa: BLE001
        reparaciones = []

    ordenes = []
    for r in reparaciones:
        estado = r.get("estado") or ""
        ordenes.append({
            "codigo": f"OS-{r.get('id')}",
            "problema": r.get("problema"),
            "tipo": r.get("tipo_atencion"),
            "estado": ESTADOS_LABEL.get(estado, estado),
            "badge_class": _clase_estado(estado),
            "ingreso": (r.get("fecha_ingreso") or "")[:10],
        })

    stats = [
        {"label": "Reparaciones en servicio", "value": _contar("reparaciones", estado="EN_REPARACION") or 0, "icon": "🔧"},
        {"label": "Listas para entrega", "value": _contar("reparaciones", estado="LISTO_ENTREGA") or 0, "icon": "📦"},
        {"label": "Entregadas", "value": _contar("reparaciones", estado="ENTREGADO") or 0, "icon": "✅"},
        {"label": "Clientes", "value": _contar("clientes") or 0, "icon": "👥"},
    ]
    return render_template("worker/dashboard.html", stats=stats, ordenes=ordenes)