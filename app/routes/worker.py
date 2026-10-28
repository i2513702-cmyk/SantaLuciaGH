"""Panel del técnico/worker (datos reales vía API REST de Supabase).

Además de las reparaciones, muestra las citas asignadas al técnico
logueado y permite gestionar su disponibilidad (agenda_tecnicos).
"""

import logging
from datetime import date, datetime, timedelta

from flask import (
    Blueprint,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.decorators import roles_required, tecnico_required
from app.services.cita_service import SEPARADOR_DIRECCION, marcar_primera_atencion
from app.services.pdf_service import generar_orden_servicio_pdf
from app.services.storage_service import asegurar_bucket, subir_imagen
from app.supabase_client import error_postgrest, get_admin_client, get_reader

log = logging.getLogger(__name__)

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

ESTADOS_EN_SERVICIO = (
    "RECIBIDO",
    "DIAGNOSTICO",
    "ESPERANDO_APROBACION",
    "APROBADO",
    "ESPERANDO_REPUESTO",
    "EN_REPARACION",
    "REPARADO",
    "LISTO_ENTREGA",
)

TRANSICIONES_REPARACION = {
    "RECIBIDO": {"DIAGNOSTICO", "NO_REPARABLE", "CANCELADO"},
    "DIAGNOSTICO": {"ESPERANDO_APROBACION", "EN_REPARACION", "NO_REPARABLE", "CANCELADO"},
    "ESPERANDO_APROBACION": {"APROBADO", "NO_REPARABLE", "CANCELADO"},
    "APROBADO": {"EN_REPARACION", "NO_REPARABLE", "CANCELADO"},
    "ESPERANDO_REPUESTO": {"EN_REPARACION", "NO_REPARABLE", "CANCELADO"},
    "EN_REPARACION": {"REPARADO", "NO_REPARABLE", "CANCELADO"},
    "REPARADO": {"LISTO_ENTREGA", "NO_REPARABLE", "CANCELADO"},
    "LISTO_ENTREGA": set(),
    "ENTREGADO": set(),
    "NO_REPARABLE": set(),
    "CANCELADO": set(),
}

TRANSICIONES_CITA = {
    "PENDIENTE": {"CONFIRMADA"},
    "CONFIRMADA": {"EN_ATENCION"},
    "EN_ATENCION": {"COMPLETADA"},
    "COMPLETADA": set(),
    "CANCELADA": set(),
}

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
            if valor is None:
                q = q.is_(campo, None)
            else:
                q = q.eq(campo, valor)
        resp = q.limit(1).execute()
        return resp.count
    except Exception:  # noqa: BLE001
        log.exception("No se pudo contar %s", tabla)
        return None


def _tecnico_actual():
    """Devuelve el tecnico del usuario logueado (via usuarios->empleados->tecnicos)."""
    if getattr(g, "tecnico_actual_cache", None) is not None:
        return g.tecnico_actual_cache
    if session.get("tecnico_cache"):
        g.tecnico_actual_cache = session.get("tecnico_cache")
        return g.tecnico_actual_cache
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
        tecnico = tec[0]
        if tecnico:
            session["tecnico_cache"] = tecnico
            session["tecnico_id_cache"] = tecnico.get("id")
            g.tecnico_actual_cache = tecnico
        return tecnico
    except Exception:  # noqa: BLE001
        log.exception("No se pudo obtener el técnico actual")
        return None


def _tecnico_id():
    if getattr(g, "tecnico_id_cache", None):
        return g.tecnico_id_cache
    tid = session.get("tecnico_id_cache")
    if tid:
        g.tecnico_id_cache = tid
        return tid
    tecnico = _tecnico_actual()
    tid = (tecnico or {}).get("id")
    if tid:
        g.tecnico_id_cache = tid
        session["tecnico_id_cache"] = tid
    return tid


def _citas_tecnico(tecnico_id, incluir_libres=False):
    """Citas (reservas) del técnico; con `incluir_libres` suma las que el cliente
    agendó y aún no tienen técnico (visitas a domicilio incluidas)."""
    campos = (
        "id,fecha_reserva,hora_inicio,hora_fin,estado,tipo_atencion,motivo,"
        "observaciones,calle,distrito,tecnico_id,"
        "clientes(nombres,apellidos,telefono,numero_documento,razon_social),"
        "electrodomesticos(id,modelo,numero_serie,marcas(nombre),tipos_electrodomestico(nombre))"
    )
    filas = []
    try:
        if tecnico_id:
            filas += (
                get_reader().table("reservas").select(campos)
                .eq("tecnico_id", tecnico_id)
                .order("fecha_reserva", desc=True).order("hora_inicio", desc=True)
                .limit(80).execute()
            ).data or []
        if incluir_libres:
            q = get_reader().table("reservas").select(campos).is_("tecnico_id", "null").neq("estado", "CANCELADA")
            if tecnico_id:
                # evitar duplicados si ya existe
                pass
            filas += (
                q.order("fecha_reserva", desc=True).order("hora_inicio", desc=True)
                .limit(80).execute()
            ).data or []
    except Exception:  # noqa: BLE001
        log.exception("No se pudieron cargar las citas del técnico")
        return []
    # evitar duplicados por id
    vistos = set()
    citas = []
    for r in filas:
        rid = r.get("id")
        if rid in vistos:
            continue
        vistos.add(rid)
        estado = r.get("estado") or "PENDIENTE"
        cliente = r.get("clientes") or {}
        equipo = r.get("electrodomesticos") or {}
        calle = (r.get("calle") or "").strip()
        distrito = (r.get("distrito") or "").strip()
        direccion = calle
        if SEPARADOR_DIRECCION in calle and not distrito:
            distrito, _, direccion = calle.partition(SEPARADOR_DIRECCION)
        if not direccion and calle and distrito and calle == distrito:
            direccion = ""
        citas.append({
            "id": r.get("id"),
            "fecha": (r.get("fecha_reserva") or "")[:10],
            "hora": (r.get("hora_inicio") or "")[:5],
            "hora_fin": (r.get("hora_fin") or "")[:5],
            "estado": ESTADO_CITA_LABEL.get(estado, estado),
            "estado_raw": estado,
            "badge_class": _clase_estado_cita(estado),
            "tipo_atencion": r.get("tipo_atencion"),
            "motivo": r.get("motivo"),
            "observaciones": r.get("observaciones"),
            "distrito": distrito,
            "direccion": direccion or calle,
            "calle": calle,
            "mia": r.get("tecnico_id") == tecnico_id,
            "tecnico_id": r.get("tecnico_id"),
            "cliente_id": cliente.get("id") if isinstance(cliente, dict) else None,
            "cliente_nombre": cliente.get("razon_social")
            or " ".join(filter(None, [cliente.get("nombres"), cliente.get("apellidos")])),
            "cliente_telefono": cliente.get("telefono"),
            "cliente_documento": cliente.get("numero_documento"),
            "equipo_id": (equipo or {}).get("id"),
            "equipo": " ".join(filter(None, [
                (equipo.get("tipos_electrodomestico") or {}).get("nombre"),
                (equipo.get("marcas") or {}).get("nombre"),
                equipo.get("modelo"),
            ])),
            "modelo": equipo.get("modelo"),
            "numero_serie": equipo.get("numero_serie"),
        })
    if incluir_libres:
        hoy = date.today().isoformat()
        prox = sorted((c for c in citas if c["fecha"] >= hoy), key=lambda c: (c["fecha"], c["hora"]))
        pasadas = sorted((c for c in citas if c["fecha"] < hoy), key=lambda c: (c["fecha"], c["hora"]), reverse=True)
        citas = prox + pasadas
    else:
        citas = sorted(citas, key=lambda c: (c["fecha"], c["hora"]))
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
            .limit(80)
            .execute()
        )
        filas = resp.data or []
    except Exception:  # noqa: BLE001
        log.exception("No se pudo cargar la disponibilidad del técnico")
        return []
    return [{
        "id": r.get("id"),
        "fecha": (r.get("fecha") or "")[:10],
        "hora_inicio": r.get("hora_inicio"),
        "hora_fin": r.get("hora_fin"),
        "disponible": r.get("disponible") if r.get("disponible") is not None else True,
    } for r in filas]


@bp.route("/worker")
@roles_required("worker")
def dashboard():
    tecnico = _tecnico_actual()
    tecnico_id = _tecnico_id() or (tecnico or {}).get("id")

    reparaciones = []
    if tecnico_id:
        try:
            reparaciones = (
                get_reader()
                .table("reparaciones")
                .select("*")
                .eq("tecnico_id", tecnico_id)
                .order("fecha_ingreso", desc=True)
                .limit(10)
                .execute()
            ).data or []
        except Exception:  # noqa: BLE001
            log.exception("No se pudieron cargar las reparaciones del técnico")
            flash("No se pudo cargar las reparaciones. Inténtalo nuevamente.", "warning")
            reparaciones = []
    else:
        # Si no hay técnico vinculado, no mostrar reparaciones ajenas
        reparaciones = []

    ordenes = []
    for r in reparaciones:
        estado = r.get("estado") or ""
        ordenes.append({
            "id": r.get("id"),
            "codigo": f"OS-{r.get('id')}",
            "problema": r.get("problema"),
            "tipo": r.get("tipo_atencion"),
            "estado": ESTADOS_LABEL.get(estado, estado),
            "badge_class": _clase_estado(estado),
            "ingreso": (r.get("fecha_ingreso") or "")[:10],
        })

    citas_panel = _citas_tecnico(tecnico_id, incluir_libres=True) if tecnico_id else []
    n_mias = sum(1 for c in citas_panel if c["mia"])

    hoy_iso = date.today().isoformat()
    citas_asignadas_hoy_futuro = 0
    if tecnico_id:
        try:
            resp_c = (
                get_reader()
                .table("reservas")
                .select("id", count="exact")
                .eq("tecnico_id", tecnico_id)
                .in_("estado", ["PENDIENTE", "CONFIRMADA", "EN_ATENCION"])
                .gte("fecha_reserva", hoy_iso)
                .limit(1)
                .execute()
            )
            citas_asignadas_hoy_futuro = resp_c.count or 0
        except Exception:  # noqa: BLE001
            log.exception("No se pudo contar citas asignadas del técnico")
            citas_asignadas_hoy_futuro = n_mias

    def _contar_rep(estado):
        if not tecnico_id:
            return 0
        c = _contar("reparaciones", tecnico_id=tecnico_id, estado=estado)
        return c if c is not None else 0

    stats = [
        {"label": "En reparación", "value": _contar_rep("EN_REPARACION"), "icon": "🔧", "filtro": "EN_REPARACION"},
        {"label": "Listas para entrega", "value": _contar_rep("LISTO_ENTREGA"), "icon": "📦", "filtro": "LISTO_ENTREGA"},
        {"label": "Entregadas", "value": _contar_rep("ENTREGADO"), "icon": "✅", "filtro": "ENTREGADO"},
        {"label": "Citas asignadas", "value": citas_asignadas_hoy_futuro, "icon": "📅", "filtro": "CITAS"},
    ]
    return render_template(
        "worker/dashboard.html",
        stats=stats,
        ordenes=ordenes,
        tecnico=tecnico,
        citas=citas_panel,
        n_citas_libres=sum(1 for c in citas_panel if not c["mia"]),
        disponibilidad=_disponibilidad_tecnico(tecnico_id) if tecnico_id else [],
        es_tecnico=tecnico is not None,
    )


RESUMEN_POR_PAGINA = 10


def _paginar(filas: list, q: str) -> tuple:
    """Filtra por `busqueda` y devuelve (page_rows, meta) de 10 en 10."""
    q = (q or "").strip()
    if q:
        filas = [f for f in filas if q.lower() in (f.get("busqueda") or "").lower()]

    total = len(filas)
    paginas = max(1, -(-total // RESUMEN_POR_PAGINA))
    try:
        pagina = int(request.args.get("pagina") or 1)
    except ValueError:
        pagina = 1
    pagina = min(max(pagina, 1), paginas)
    inicio = (pagina - 1) * RESUMEN_POR_PAGINA

    return filas[inicio:inicio + RESUMEN_POR_PAGINA], {
        "pagina": pagina,
        "paginas": paginas,
        "total": total,
        "por_pagina": RESUMEN_POR_PAGINA,
    }


@bp.get("/worker/resumen/<clave>")
@roles_required("worker")
def resumen_detalle(clave):
    """JSON paginado y filtrable del detalle de una tarjeta del 'Resumen'."""
    tecnico_id = _tecnico_id()
    if clave == "CITAS":
        tecnico = _tecnico_actual()
        citas = _citas_tecnico(tecnico_id or (tecnico or {}).get("id")) if (tecnico_id or tecnico) else []
        formateadas = [{
            "celdas": [c["fecha"], c["hora"] or "—", c["cliente_nombre"], c["equipo"] or "—"],
            "badge": {"texto": c["estado"], "clase": c["badge_class"]},
            "busqueda": c["cliente_nombre"],
        } for c in citas]
        pagina, meta = _paginar(formateadas, request.args.get("q"))
        return jsonify({
            "ok": True,
            "titulo": "Citas asignadas",
            "encabezados": ["Fecha", "Hora", "Cliente", "Equipo", "Estado"],
            "filas": [{"celdas": f["celdas"], "badge": f["badge"]} for f in pagina],
            "placeholder": "Buscar por cliente...",
            **meta,
        })

    estados = {
        "EN_REPARACION": "En reparación",
        "LISTO_ENTREGA": "Listas para entrega",
        "ENTREGADO": "Entregadas",
    }
    if clave not in estados:
        return jsonify({"ok": False, "detalle": "Indicador no encontrado."}), 404

    q = (request.args.get("q") or "").strip()
    try:
        pagina = int(request.args.get("pagina") or 1)
    except ValueError:
        pagina = 1
    pagina = max(1, pagina)
    inicio = (pagina - 1) * RESUMEN_POR_PAGINA
    fin = inicio + RESUMEN_POR_PAGINA - 1

    try:
        query = (
            get_reader()
            .table("reparaciones")
            .select("id,problema,tipo_atencion,estado,fecha_ingreso", count="exact")
            .eq("estado", clave)
            .order("fecha_ingreso", desc=True)
        )
        if tecnico_id:
            query = query.eq("tecnico_id", tecnico_id)
        if q:
            # filtra en BD para evitar traer todo
            query = query.ilike("problema", f"%{q}%")
        resp = query.range(inicio, fin).execute()
        filas_bd = resp.data or []
        total = resp.count if resp.count is not None else len(filas_bd)
    except Exception as exc:  # noqa: BLE001
        log.exception("No se pudo cargar resumen_detalle para %s", clave)
        return jsonify({"ok": False, "detalle": "No se pudo cargar el contenido."}), 502

    formateadas = [{
        "celdas": [
            f"OS-{r.get('id')}",
            r.get("problema") or "—",
            (r.get("fecha_ingreso") or "")[:10] or "—",
            r.get("tipo_atencion") or "—",
        ],
        "badge": {"texto": ESTADOS_LABEL.get(r.get("estado") or "", r.get("estado") or "—"),
                  "clase": _clase_estado(r.get("estado") or "")},
        "busqueda": r.get("problema") or "",
    } for r in filas_bd]

    paginas = max(1, -(-total // RESUMEN_POR_PAGINA))
    pagina = min(pagina, paginas)

    return jsonify({
        "ok": True,
        "titulo": estados[clave],
        "encabezados": ["Orden", "Problema", "Ingreso", "Atención", "Estado"],
        "filas": [{"celdas": f["celdas"], "badge": f["badge"]} for f in formateadas],
        "placeholder": "Buscar por problema...",
        "pagina": pagina,
        "paginas": paginas,
        "total": total,
        "por_pagina": RESUMEN_POR_PAGINA,
    })


@bp.post("/worker/agenda/agregar")
@roles_required("worker")
@tecnico_required
def agenda_agregar():
    """Agrega una o varias franjas de disponibilidad para el técnico logueado."""
    tecnico = _tecnico_actual()
    if not tecnico:
        flash("Tu cuenta no está vinculada a un técnico.", "error")
        return redirect(url_for("worker.dashboard"))
    tecnico_id = tecnico["id"]

    fechas = request.form.getlist("fechas")
    if not fechas:
        f = (request.form.get("fecha") or "").strip()
        if f:
            fechas = [f]
    hora_inicio = (request.form.get("hora_inicio") or "").strip()
    hora_fin = (request.form.get("hora_fin") or "").strip()
    disponible = request.form.get("disponible") != "false"

    try:
        from datetime import date as date_cls

        if not fechas:
            raise ValueError("indicar fecha")
        if len(hora_inicio) != 5 or len(hora_fin) != 5:
            raise ValueError("formato hora")
        datetime.strptime(hora_inicio, "%H:%M")
        datetime.strptime(hora_fin, "%H:%M")
        if hora_inicio >= hora_fin:
            raise ValueError("rango hora")
        hoy = date_cls.today()
        for f in fechas:
            f2 = (f or "").strip()
            if not f2:
                continue
            fecha_ok = datetime.strptime(f2, "%Y-%m-%d").date()
            if fecha_ok < hoy:
                raise ValueError("fecha pasada")
    except ValueError:
        flash("Datos inválidos. Revisa fecha y horas.", "error")
        return redirect(url_for("worker.dashboard"))
    except Exception:
        log.exception("Error validando agenda_agregar")
        flash("No se pudo agregar la franja.", "error")
        return redirect(url_for("worker.dashboard"))

    try:
        db = get_admin_client()
        # evitar traslapes
        for f2 in fechas:
            f2s = (f2 or "").strip()
            if not f2s:
                continue
            existentes = (
                get_reader()
                .table("agenda_tecnicos")
                .select("id,hora_inicio,hora_fin")
                .eq("tecnico_id", tecnico_id)
                .eq("fecha", f2s)
                .execute()
                .data
                or []
            )
            for ex in existentes:
                hi = (ex.get("hora_inicio") or "")[:5]
                hf = (ex.get("hora_fin") or "")[:5]
                if not hi or not hf:
                    continue
                # traslape si no (hf <= inicio or fin <= hi)
                if not (hf <= hora_inicio or hora_fin <= hi):
                    flash("Ya existe una franja traslapada para esa fecha.", "error")
                    return redirect(url_for("worker.dashboard"))
            db.table("agenda_tecnicos").insert({
                "tecnico_id": tecnico_id,
                "fecha": f2s,
                "hora_inicio": hora_inicio,
                "hora_fin": hora_fin,
                "disponible": bool(disponible),
            }).execute()
        flash("Disponibilidad agregada.", "success")
    except Exception:
        log.exception("No se pudo agregar franja de agenda")
        flash("No se pudo agregar la franja.", "error")
    return redirect(url_for("worker.dashboard"))


@bp.post("/worker/agenda/<int:franja_id>/quitar")
@roles_required("worker")
@tecnico_required
def agenda_quitar(franja_id):
    """Elimina una franja de disponibilidad del técnico logueado."""
    tecnico = _tecnico_actual()
    if not tecnico:
        flash("Tu cuenta no está vinculada a un técnico.", "error")
        return redirect(url_for("worker.dashboard"))
    try:
        # verificar que no tenga cita CONFIRMADA/EN_ATENCION en rango
        franja = (
            get_reader()
            .table("agenda_tecnicos")
            .select("id,tecnico_id,fecha,hora_inicio,hora_fin")
            .eq("id", franja_id)
            .eq("tecnico_id", tecnico["id"])
            .limit(1)
            .execute()
            .data
            or []
        )
        if not franja:
            flash("Franja no encontrada.", "warning")
            return redirect(url_for("worker.dashboard"))
        f = franja[0]
        # buscar reservas del técnico ese día en estados activos que solapen
        try:
            reservas = (
                get_reader()
                .table("reservas")
                .select("id,hora_inicio,hora_fin,estado")
                .eq("tecnico_id", tecnico["id"])
                .eq("fecha_reserva", f.get("fecha"))
                .in_("estado", ["CONFIRMADA", "EN_ATENCION"])
                .execute()
                .data
                or []
            )
            hi_f = (f.get("hora_inicio") or "")[:5]
            hf_f = (f.get("hora_fin") or "")[:5]
            for r in reservas:
                hi_r = (r.get("hora_inicio") or "")[:5]
                hf_r = (r.get("hora_fin") or "")[:5]
                if not hi_r or not hf_r:
                    continue
                if not (hf_f <= hi_r or hf_r <= hi_f):
                    flash("No se puede eliminar una franja que tiene una cita CONFIRMADA o EN_ATENCION.", "error")
                    return redirect(url_for("worker.dashboard"))
        except Exception:
            log.exception("Error verificando citas al quitar franja")

        resp_del = get_admin_client().table("agenda_tecnicos").delete().eq("id", franja_id).eq("tecnico_id", tecnico["id"]).execute()
        # si no borra nada
        if not getattr(resp_del, "data", None):
            flash("Franja no encontrada.", "warning")
            return redirect(url_for("worker.dashboard"))
        flash("Franja de disponibilidad eliminada.", "success")
    except Exception:
        log.exception("No se pudo eliminar la franja")
        flash("No se pudo eliminar la franja.", "error")
    return redirect(url_for("worker.dashboard"))


@bp.post("/worker/agenda/<int:franja_id>/toggle")
@roles_required("worker")
@tecnico_required
def agenda_toggle(franja_id):
    tecnico = _tecnico_actual()
    if not tecnico:
        flash("Tu cuenta no está vinculada a un técnico.", "error")
        return redirect(url_for("worker.dashboard"))
    try:
        franja = (
            get_reader()
            .table("agenda_tecnicos")
            .select("id,disponible")
            .eq("id", franja_id)
            .eq("tecnico_id", tecnico["id"])
            .limit(1)
            .execute()
            .data
            or []
        )
        if not franja:
            flash("Franja no encontrada.", "warning")
            return redirect(url_for("worker.dashboard"))
        nuevo = not bool(franja[0].get("disponible"))
        get_admin_client().table("agenda_tecnicos").update({"disponible": nuevo}).eq("id", franja_id).eq("tecnico_id", tecnico["id"]).execute()
        flash("Disponibilidad actualizada.", "success")
    except Exception:
        log.exception("No se pudo actualizar la franja")
        flash("No se pudo actualizar la franja.", "error")
    return redirect(url_for("worker.dashboard"))