"""Panel del técnico/worker (datos reales vía API REST de Supabase).

Además de las reparaciones, muestra las citas asignadas al técnico
logueado y permite gestionar su disponibilidad (agenda_tecnicos).
"""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.decorators import roles_required
from app.supabase_client import get_admin_client, get_reader

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

ESTADO_CITA_LABEL = {
    "PENDIENTE": "Pendiente",
    "CONFIRMADA": "Confirmada",
    "EN_ATENCION": "En atención",
    "COMPLETADA": "Completada",
    "CANCELADA": "Cancelada",
}


def _clase_estado(estado: str) -> str:
    if estado in ("ENTREGADO", "REPARADO", "LISTO_ENTREGA"):
        return "badge-ok"
    if estado in ("EN_REPARACION", "DIAGNOSTICO", "APROBADO"):
        return "badge-warn"
    return "badge-muted"


def _clase_estado_cita(estado: str) -> str:
    if estado == "COMPLETADA":
        return "badge-ok"
    if estado in ("CONFIRMADA", "EN_ATENCION"):
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


def _tecnico_actual():
    """Devuelve el tecnico del usuario logueado (via usuarios->empleados->tecnicos)."""
    usuario_id = session.get("usuario_id")
    if not usuario_id:
        return None
    try:
        emp = (
            get_reader()
            .table("usuarios")
            .select("empleado_id")
            .eq("id", usuario_id)
            .limit(1)
            .execute()
        ).data or [{}]
        empleado_id = (emp[0] or {}).get("empleado_id")
        if not empleado_id:
            return None
        tec = (
            get_reader()
            .table("tecnicos")
            .select("id,empleado_id,especialidad,empleados(nombres,apellidos)")
            .eq("empleado_id", empleado_id)
            .limit(1)
            .execute()
        ).data or [None]
        return tec[0]
    except Exception:  # noqa: BLE001
        return None


def _citas_tecnico(tecnico_id):
    """Citas (reservas) asignadas al técnico."""
    try:
        resp = (
            get_reader()
            .table("reservas")
            .select(
                "id,fecha_reserva,hora_inicio,hora_fin,estado,tipo_atencion,motivo,"
                "clientes(nombres,apellidos,telefono,numero_documento,razon_social),"
                "electrodomesticos(modelo,numero_serie,marcas(nombre),tipos_electrodomestico(nombre))"
            )
            .eq("tecnico_id", tecnico_id)
            .order("fecha_reserva", desc=True)
            .order("hora_inicio", desc=True)
            .limit(30)
            .execute()
        )
        filas = resp.data or []
    except Exception:  # noqa: BLE001
        return []
    citas = []
    for r in filas:
        estado = r.get("estado") or "PENDIENTE"
        cliente = r.get("clientes") or {}
        equipo = r.get("electrodomesticos") or {}
        citas.append({
            "id": r.get("id"),
            "fecha": (r.get("fecha_reserva") or "")[:10],
            "hora": r.get("hora_inicio"),
            "hora_fin": r.get("hora_fin"),
            "estado": ESTADO_CITA_LABEL.get(estado, estado),
            "badge_class": _clase_estado_cita(estado),
            "tipo_atencion": r.get("tipo_atencion"),
            "motivo": r.get("motivo"),
            "cliente_nombre": cliente.get("razon_social")
            or " ".join(filter(None, [cliente.get("nombres"), cliente.get("apellidos")])),
            "cliente_telefono": cliente.get("telefono"),
            "equipo": " ".join(filter(None, [
                (equipo.get("tipos_electrodomestico") or {}).get("nombre"),
                (equipo.get("marcas") or {}).get("nombre"),
                equipo.get("modelo"),
            ])),
        })
    return citas


def _disponibilidad_tecnico(tecnico_id):
    """Franjas de disponibilidad del técnico en agenda_tecnicos."""
    try:
        resp = (
            get_reader()
            .table("agenda_tecnicos")
            .select("id,fecha,hora_inicio,hora_fin,disponible")
            .eq("tecnico_id", tecnico_id)
            .order("fecha", desc=True)
            .order("hora_inicio")
            .limit(40)
            .execute()
        )
        filas = resp.data or []
    except Exception:  # noqa: BLE001
        return []
    return [{
        "id": r.get("id"),
        "fecha": (r.get("fecha") or "")[:10],
        "hora_inicio": r.get("hora_inicio"),
        "hora_fin": r.get("hora_fin"),
        "disponible": r.get("disponible"),
    } for r in filas]


@bp.route("/worker")
@roles_required("worker")
def dashboard():
    tecnico = _tecnico_actual()
    tecnico_id = (tecnico or {}).get("id")

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
        {"label": "Citas asignadas", "value": len(_citas_tecnico(tecnico_id)) if tecnico_id else 0, "icon": "📅"},
    ]
    return render_template(
        "worker/dashboard.html",
        stats=stats,
        ordenes=ordenes,
        tecnico=tecnico,
        citas=_citas_tecnico(tecnico_id) if tecnico_id else [],
        disponibilidad=_disponibilidad_tecnico(tecnico_id) if tecnico_id else [],
        es_tecnico=tecnico is not None,
    )


@bp.post("/worker/agenda/agregar")
@roles_required("worker")
def agenda_agregar():
    """Agrega una franja de disponibilidad para el técnico logueado."""
    tecnico = _tecnico_actual()
    if not tecnico:
        flash("Tu cuenta no está vinculada a un técnico.", "error")
        return redirect(url_for("worker.dashboard"))

    fecha = (request.form.get("fecha") or "").strip()
    hora_inicio = (request.form.get("hora_inicio") or "").strip()
    hora_fin = (request.form.get("hora_fin") or "").strip()
    if not fecha or len(fecha) != 10:
        flash("Indica la fecha de tu disponibilidad.", "error")
    elif hora_inicio >= hora_fin:
        flash("La hora de fin debe ser mayor que la de inicio.", "error")
    else:
        try:
            get_admin_client().table("agenda_tecnicos").insert({
                "tecnico_id": tecnico["id"],
                "fecha": fecha,
                "hora_inicio": hora_inicio,
                "hora_fin": hora_fin,
                "disponible": True,
            }).execute()
            flash("Disponibilidad agregada.", "success")
        except Exception as exc:  # noqa: BLE001
            flash(f"No se pudo agregar la franja: {exc}", "error")
    return redirect(url_for("worker.dashboard"))


@bp.post("/worker/agenda/<int:franja_id>/quitar")
@roles_required("worker")
def agenda_quitar(franja_id):
    """Elimina una franja de disponibilidad del técnico logueado."""
    tecnico = _tecnico_actual()
    if not tecnico:
        flash("Tu cuenta no está vinculada a un técnico.", "error")
        return redirect(url_for("worker.dashboard"))
    try:
        get_admin_client().table("agenda_tecnicos").delete().eq("id", franja_id).execute()
        flash("Franja de disponibilidad eliminada.", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"No se pudo eliminar la franja: {exc}", "error")
    return redirect(url_for("worker.dashboard"))