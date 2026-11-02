"""Panel del técnico/worker (datos reales vía API REST de Supabase).

Además de las reparaciones, muestra las citas asignadas al técnico
logueado y permite gestionar su disponibilidad (agenda_tecnicos).
"""

import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from flask import (
    Blueprint,
    Response,
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
from app.exceptions import ValidationError
from app.services import agenda_service, storage_service
from app.services.cita_service import (
    SEPARADOR_DIRECCION,
    _cubre_franja,
    marcar_primera_atencion,
    nombre_equipo,
)
from app.services.pdf_service import generar_orden_servicio_pdf
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
    "DIAGNOSTICO": {"ESPERANDO_APROBACION", "EN_REPARACION", "ESPERANDO_REPUESTO", "NO_REPARABLE", "CANCELADO"},
    "ESPERANDO_APROBACION": {"APROBADO", "NO_REPARABLE", "CANCELADO"},
    "APROBADO": {"EN_REPARACION", "ESPERANDO_REPUESTO", "NO_REPARABLE", "CANCELADO"},
    "ESPERANDO_REPUESTO": {"EN_REPARACION", "NO_REPARABLE", "CANCELADO"},
    "EN_REPARACION": {"REPARADO", "ESPERANDO_REPUESTO", "NO_REPARABLE", "CANCELADO"},
    "REPARADO": {"LISTO_ENTREGA", "NO_REPARABLE", "CANCELADO"},
    "LISTO_ENTREGA": {"ENTREGADO"},
    "ENTREGADO": set(),
    "NO_REPARABLE": set(),
    "CANCELADO": set(),
}

# Estado del equipo (electrodomesticos.estado) según la reparación.
ESTADO_EQUIPO = {
    "EN_REPARACION": "EN_REPARACION",
    "REPARADO": "REPARADO",
    "ENTREGADO": "ENTREGADO",
    "NO_REPARABLE": "NO_REPARABLE",
}

# Estados reales de `reservas.estado` (CHECK de la base de datos).
TRANSICIONES_CITA = {
    "PENDIENTE": {"CONFIRMADA"},
    "ASIGNADA": {"CONFIRMADA", "EN_ATENCION", "NO_ASISTIO"},
    "CONFIRMADA": {"EN_ATENCION", "NO_ASISTIO"},
    "EN_ATENCION": {"ATENDIDA"},
    "ATENDIDA": set(),
    "CANCELADA": set(),
    "NO_ASISTIO": set(),
}

ESTADOS_CITA_ABIERTOS = ("PENDIENTE", "CONFIRMADA", "ASIGNADA", "EN_ATENCION")

ESTADO_CITA_LABEL = {
    "PENDIENTE": "Pendiente",
    "CONFIRMADA": "Confirmada",
    "ASIGNADA": "Asignada",
    "EN_ATENCION": "En atención",
    "ATENDIDA": "Atendida",
    "CANCELADA": "Cancelada",
    "NO_ASISTIO": "No asistió",
}

MAX_FOTOS_REPARACION = 12
CACHE_TECNICO_SEG = 300


def _clase_estado(estado: str) -> str:
    if estado in ("ENTREGADO", "REPARADO", "LISTO_ENTREGA"):
        return "badge-ok"
    if estado in ("EN_REPARACION", "DIAGNOSTICO", "APROBADO"):
        return "badge-warn"
    return "badge-muted"


def _clase_estado_cita(estado: str) -> str:
    if estado == "ATENDIDA":
        return "badge-ok"
    if estado in ("CONFIRMADA", "ASIGNADA", "EN_ATENCION"):
        return "badge-warn"
    if estado == "CANCELADA":
        return "badge-muted"
    return "badge-muted"


def _hoy_lima() -> date:
    """Fecha de hoy en Lima (America/Lima). El servidor corre en UTC."""
    return agenda_service.hoy_lima()


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """Devuelve el técnico del usuario logueado (via usuarios->empleados->tecnicos).

    Se guarda en la sesión solo CACHE_TECNICO_SEG segundos: así un cambio de
    especialidad o de vínculo se refleja sin tener que volver a iniciar sesión.
    """
    if getattr(g, "tecnico_actual_cache", None) is not None:
        return g.tecnico_actual_cache
    ahora = time.time()
    cache = session.get("tecnico_cache")
    if cache and ahora - session.get("tecnico_cache_ts", 0) < CACHE_TECNICO_SEG:
        g.tecnico_actual_cache = cache
        return cache
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
        tecnico = None
        if empleado_id:
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
            session["tecnico_cache_ts"] = ahora
            session["tecnico_id_cache"] = tecnico.get("id")
            g.tecnico_actual_cache = tecnico
        else:
            for clave in ("tecnico_cache", "tecnico_cache_ts", "tecnico_id_cache"):
                session.pop(clave, None)
        return tecnico
    except Exception:  # noqa: BLE001
        log.exception("No se pudo obtener el técnico actual")
        return None


def _tecnico_id():
    return (_tecnico_actual() or {}).get("id")


def _es_unico_duplicado(exc) -> bool:
    """True si el error de Postgres es una violación de unicidad (23505)."""
    texto = str(exc).lower()
    return "23505" in texto or "duplicate key" in texto or "uq_reservas_tecnico_horario" in texto


def _citas_tecnico(tecnico_id, incluir_libres=False):
    """Citas (reservas) del técnico; con `incluir_libres` suma las que el cliente
    agendó, siguen PENDIENTES, no tienen técnico y aún no vencen (visitas a
    domicilio incluidas) para que el técnico pueda tomarlas."""
    campos = (
        "id,fecha_reserva,hora_inicio,hora_fin,estado,tipo_atencion,motivo,"
        "observaciones,calle,distrito,tecnico_id,"
        "clientes(id,nombres,apellidos,telefono,numero_documento,razon_social),"
        "electrodomesticos(id,modelo,numero_serie,tipo_otro,marca_otro,marcas(nombre),tipos_electrodomestico(nombre))"
    )
    hoy = _hoy_lima().isoformat()
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
            filas += (
                get_reader().table("reservas").select(campos)
                .is_("tecnico_id", "null")
                .eq("estado", "PENDIENTE")
                .gte("fecha_reserva", hoy)
                .order("fecha_reserva").order("hora_inicio")
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
        fecha = (r.get("fecha_reserva") or "")[:10]
        citas.append({
            "id": r.get("id"),
            "fecha": fecha,
            "hora": (r.get("hora_inicio") or "")[:5],
            "hora_fin": (r.get("hora_fin") or "")[:5],
            "estado": ESTADO_CITA_LABEL.get(estado, estado),
            "estado_raw": estado,
            "badge_class": _clase_estado_cita(estado),
            "vencida": bool(fecha and fecha < hoy and estado in ESTADOS_CITA_ABIERTOS),
            "tipo_atencion": r.get("tipo_atencion"),
            "motivo": r.get("motivo"),
            "observaciones": r.get("observaciones"),
            "distrito": distrito,
            "direccion": direccion or calle,
            "calle": calle,
            "mia": bool(tecnico_id) and r.get("tecnico_id") == tecnico_id,
            "tecnico_id": r.get("tecnico_id"),
            "cliente_id": cliente.get("id") if isinstance(cliente, dict) else None,
            "cliente_nombre": cliente.get("razon_social")
            or " ".join(filter(None, [cliente.get("nombres"), cliente.get("apellidos")])),
            "cliente_telefono": cliente.get("telefono"),
            "cliente_documento": cliente.get("numero_documento"),
            "equipo_id": (equipo or {}).get("id"),
            "equipo": nombre_equipo(equipo),
            "modelo": equipo.get("modelo"),
            "numero_serie": equipo.get("numero_serie"),
        })
    if incluir_libres:
        prox = sorted((c for c in citas if c["fecha"] >= hoy), key=lambda c: (c["fecha"], c["hora"]))
        pasadas = sorted((c for c in citas if c["fecha"] < hoy), key=lambda c: (c["fecha"], c["hora"]), reverse=True)
        citas = prox + pasadas
    else:
        citas = sorted(citas, key=lambda c: (c["fecha"], c["hora"]))
    return citas


def _disponibilidad_tecnico(tecnico_id):
    """Sesiones de disponibilidad del técnico (agenda_tecnicos) con su estado real.

    `estado` es ACTIVA / INACTIVA / VENCIDA; una sesión cuya fecha/hora de fin ya pasó
    figura como VENCIDA aunque el job de cierre todavía no la haya actualizado.
    Primero van las vigentes (la más próxima arriba) y después el historial vencido.
    """
    try:
        resp = (
            get_reader()
            .table("agenda_tecnicos")
            .select("id,fecha,hora_inicio,hora_fin,disponible,estado")
            .eq("tecnico_id", tecnico_id)
            .order("fecha", desc=True)
            .order("hora_inicio")
            .limit(300)
            .execute()
        )
        filas = resp.data or []
    except Exception:  # noqa: BLE001
        log.exception("No se pudo cargar la disponibilidad del técnico")
        return []
    ahora = agenda_service.ahora_lima()
    sesiones = []
    for r in filas:
        fecha = (r.get("fecha") or "")[:10]
        disponible = r.get("disponible") if r.get("disponible") is not None else True
        estado = agenda_service.estado_efectivo(fecha, r.get("hora_fin") or "", disponible, r.get("estado"), ahora)
        sesiones.append({
            "id": r.get("id"),
            "fecha": fecha,
            "hora_inicio": (r.get("hora_inicio") or "")[:5],
            "hora_fin": (r.get("hora_fin") or "")[:5],
            "disponible": disponible,
            "estado": estado,
            "vencida": estado == agenda_service.ESTADO_VENCIDA,
        })
    vigentes = sorted((x for x in sesiones if not x["vencida"]), key=lambda x: (x["fecha"], x["hora_inicio"]))
    vencidas = sorted((x for x in sesiones if x["vencida"]), key=lambda x: (x["fecha"], x["hora_inicio"]), reverse=True)
    return vigentes + vencidas


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

    def _contar_cita_estado(estado):
        if not tecnico_id:
            return 0
        try:
            resp_c = (
                get_reader()
                .table("reservas")
                .select("id", count="exact")
                .eq("tecnico_id", tecnico_id)
                .eq("estado", estado)
                .limit(1)
                .execute()
            )
            return resp_c.count or 0
        except Exception:
            log.exception("No se pudo contar citas por estado %s", estado)
            return 0

    def _contar_rep(estado):
        if not tecnico_id:
            return 0
        c = _contar("reparaciones", tecnico_id=tecnico_id, estado=estado)
        return c if c is not None else 0

    stats = [
        {"label": "En reparación", "value": _contar_cita_estado("EN_ATENCION"), "icon": "🔧", "filtro": "CITAS_EN_ATENCION"},
        {"label": "Listas para entrega", "value": _contar_cita_estado("CONFIRMADA"), "icon": "📦", "filtro": "CITAS_CONFIRMADA"},
        {"label": "Entregadas", "value": _contar_cita_estado("ATENDIDA"), "icon": "✅", "filtro": "CITAS_ATENDIDA"},
        {"label": "Citas asignadas", "value": _contar_cita_estado("ASIGNADA"), "icon": "📅", "filtro": "CITAS_ASIGNADA"},
    ]

    try:
        pagina_c = int(request.args.get("citas_p") or 1)
    except ValueError:
        pagina_c = 1
    pagina_c = max(1, pagina_c)
    por_c = 10
    total_c = len(citas_panel)
    paginas_c = max(1, (total_c + por_c - 1) // por_c)
    if pagina_c > paginas_c:
        pagina_c = paginas_c
    inicio_c = (pagina_c - 1) * por_c
    citas_pagina = citas_panel[inicio_c:inicio_c + por_c] if total_c else []

    disponibilidad = _disponibilidad_tecnico(tecnico_id) if tecnico_id else []
    try:
        pagina_d = int(request.args.get("disp_p") or 1)
    except ValueError:
        pagina_d = 1
    pagina_d = max(1, pagina_d)
    por_d = 10
    total_d = len(disponibilidad)
    paginas_d = max(1, (total_d + por_d - 1) // por_d)
    if pagina_d > paginas_d:
        pagina_d = paginas_d
    inicio_d = (pagina_d - 1) * por_d
    disponibilidad_pagina = disponibilidad[inicio_d:inicio_d + por_d] if total_d else []

    return render_template(
        "worker/dashboard.html",
        stats=stats,
        ordenes=ordenes,
        tecnico=tecnico,
        citas=citas_pagina,
        citas_todas=citas_panel,
        citas_pagina=pagina_c,
        citas_paginas=paginas_c,
        citas_total=total_c,
        n_citas_libres=sum(1 for c in citas_panel if not c["mia"]),
        disponibilidad=disponibilidad_pagina,
        disponibilidad_todas=disponibilidad,
        disp_pagina=pagina_d,
        disp_paginas=paginas_d,
        disp_total=total_d,
        es_tecnico=tecnico is not None,
    )


@bp.route("/worker/reparaciones")
@roles_required("worker")
def reparaciones_listado():
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
                .execute()
            ).data or []
        except Exception:
            log.exception("No se pudieron cargar reparaciones")
            reparaciones = []
    ordenes = []
    for r in reparaciones:
        cliente = {}
        equipo = {}
        try:
            if r.get("cliente_id"):
                c = get_reader().table("clientes").select("*").eq("id", r["cliente_id"]).limit(1).execute().data or []
                cliente = c[0] if c else {}
            if r.get("electrodomestico_id"):
                e = get_reader().table("electrodomesticos").select("*").eq("id", r["electrodomestico_id"]).limit(1).execute().data or []
                equipo = e[0] if e else {}
        except Exception:
            pass
        codigo = r.get("codigo") or f"OS-{r.get('id')}"
        cliente_nom = cliente.get("razon_social") or " ".join(filter(None, [cliente.get("nombres"), cliente.get("apellidos")])) or "—"
        equipo_nom = equipo.get("descripcion") or equipo.get("modelo") or r.get("problema") or "—"
        estado = (r.get("estado") or "").upper()
        badge = {"RECIBIDO": "text-bg-secondary", "EN_DIAGNOSTICO": "text-bg-info", "EN_REPARACION": "text-bg-warning", "ESPERA_REPUESTO": "text-bg-warning", "REPARADO": "text-bg-success", "LISTO_ENTREGA": "text-bg-primary", "ENTREGADO": "text-bg-success", "CANCELADO": "text-bg-danger", "ANULADO": "text-bg-danger"}.get(estado, "text-bg-secondary")
        ingreso = (r.get("fecha_ingreso") or "")[:10]
        ordenes.append({
            "id": r.get("id"),
            "codigo": codigo,
            "problema": r.get("problema") or equipo_nom,
            "tipo": r.get("tipo_atencion") or equipo_nom,
            "ingreso": ingreso,
            "estado": ESTADOS_LABEL.get(estado, estado),
            "badge_class": badge,
            "cliente": cliente_nom,
        })
    try:
        pagina = int(request.args.get("p") or 1)
    except ValueError:
        pagina = 1
    pagina = max(1, pagina)
    por = 10
    total = len(ordenes)
    paginas = max(1, (total + por - 1) // por)
    if pagina > paginas:
        pagina = paginas
    inicio = (pagina - 1) * por
    ordenes_pag = ordenes[inicio:inicio + por] if total else []
    return render_template("worker/reparaciones.html", ordenes=ordenes_pag, pagina=pagina, paginas=paginas, total=total, tecnico=tecnico, es_tecnico=tecnico is not None)


@bp.route("/worker/citas")
@roles_required("worker")
def citas_listado():
    tecnico = _tecnico_actual()
    tecnico_id = _tecnico_id() or (tecnico or {}).get("id")
    citas_panel = _citas_tecnico(tecnico_id, incluir_libres=True) if tecnico_id else []

    tipo_f = (request.args.get("tipo") or "todas").strip().lower()
    asig_f = (request.args.get("asig") or "todas").strip().lower()
    fecha_f = (request.args.get("fecha") or "todas").strip().lower()
    q_f = (request.args.get("q") or "").strip().lower()
    hoy_iso = _hoy_lima().isoformat()

    filtradas = []
    for c in citas_panel:
        tipo = (c.get("tipo_atencion") or "").lower()
        asig = "mias" if c.get("mia") else "libres"
        fecha_c = c.get("fecha") or ""
        busq = f"{c.get('cliente_nombre') or ''} {c.get('equipo') or ''} {c.get('motivo') or ''} {c.get('observaciones') or ''}".lower()
        ok = True
        if tipo_f == "domicilio":
            ok = tipo == "domicilio"
        elif tipo_f == "taller":
            ok = tipo != "domicilio"
        if ok and asig_f == "mias":
            ok = asig == "mias"
        elif ok and asig_f == "libres":
            ok = asig == "libres"
        if ok and fecha_f == "hoy":
            ok = fecha_c == hoy_iso
        elif ok and fecha_f == "proximas":
            ok = fecha_c >= hoy_iso
        elif ok and fecha_f == "pasadas":
            ok = fecha_c < hoy_iso
        if ok and q_f:
            ok = q_f in busq
        if ok:
            filtradas.append(c)

    try:
        pagina_c = int(request.args.get("p") or 1)
    except ValueError:
        pagina_c = 1
    pagina_c = max(1, pagina_c)
    por_c = 10
    total_c = len(filtradas)
    if total_c == 0:
        paginas_c = 0
        pagina_c = 1
        citas_pagina = []
    else:
        paginas_c = max(1, (total_c + por_c - 1) // por_c)
        if pagina_c > paginas_c:
            pagina_c = paginas_c
        inicio_c = (pagina_c - 1) * por_c
        citas_pagina = filtradas[inicio_c:inicio_c + por_c]
    n_citas_libres = sum(1 for c in citas_panel if not c["mia"])
    return render_template("worker/citas.html", citas=citas_pagina, pagina=pagina_c, paginas=paginas_c, total=total_c, n_citas_libres=n_citas_libres, tecnico=tecnico, es_tecnico=tecnico is not None, f_tipo=tipo_f or "todas", f_asig=asig_f or "todas", f_fecha=fecha_f or "todas", f_q=request.args.get("q") or "")


@bp.route("/worker/disponibilidad-modulo")
@roles_required("worker")
def disponibilidad_modulo():
    agenda_service.cerrar_vencidas()  # red de seguridad del job de pg_cron (no lanza errores)
    tecnico = _tecnico_actual()
    tecnico_id = _tecnico_id() or (tecnico or {}).get("id")
    disponibilidad = _disponibilidad_tecnico(tecnico_id) if tecnico_id else []
    try:
        pagina_d = int(request.args.get("p") or 1)
    except ValueError:
        pagina_d = 1
    pagina_d = max(1, pagina_d)
    por_d = 10
    total_d = len(disponibilidad)
    paginas_d = max(1, (total_d + por_d - 1) // por_d)
    if pagina_d > paginas_d:
        pagina_d = paginas_d
    inicio_d = (pagina_d - 1) * por_d
    disponibilidad_pagina = disponibilidad[inicio_d:inicio_d + por_d] if total_d else []
    ahora = agenda_service.ahora_lima()
    return render_template(
        "worker/disponibilidad_modulo.html",
        disponibilidad=disponibilidad_pagina,
        pagina=pagina_d,
        paginas=paginas_d,
        total=total_d,
        tecnico=tecnico,
        es_tecnico=tecnico is not None,
        hoy=ahora.date().isoformat(),
        hora_actual=ahora.strftime("%H:%M"),
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
    if clave.startswith("CITAS"):
        tecnico = _tecnico_actual()
        tid = tecnico_id or (tecnico or {}).get("id")
        citas = _citas_tecnico(tid) if tid else []
        estado_map = {
            "CITAS": None,
            "CITAS_EN_ATENCION": "EN_ATENCION",
            "CITAS_CONFIRMADA": "CONFIRMADA",
            "CITAS_ATENDIDA": "ATENDIDA",
            "CITAS_ASIGNADA": "ASIGNADA",
        }
        filtro_est = estado_map.get(clave)
        if filtro_est:
            citas = [c for c in citas if (c.get("estado_raw") or "").upper() == filtro_est]
        titulos = {
            "CITAS": "Citas asignadas",
            "CITAS_EN_ATENCION": "Citas en atención",
            "CITAS_CONFIRMADA": "Citas confirmadas",
            "CITAS_ATENDIDA": "Citas atendidas",
            "CITAS_ASIGNADA": "Citas asignadas (ASIGNADA)",
        }
        formateadas = [{
            "celdas": [c["fecha"], c["hora"] or "—", c["cliente_nombre"], c["equipo"] or "—"],
            "badge": {"texto": c["estado"], "clase": c["badge_class"]},
            "busqueda": c["cliente_nombre"],
        } for c in citas]
        pagina, meta = _paginar(formateadas, request.args.get("q"))
        return jsonify({
            "ok": True,
            "titulo": titulos.get(clave, "Citas"),
            "encabezados": ["Fecha", "Hora", "Cliente", "Equipo", "Estado"],
            "filas": [{"celdas": f["celdas"], "badge": f["badge"]} for f in pagina],
            "placeholder": "Buscar por cliente...",
            **meta,
        })

    estados = {
        "RECIBIDO": "Recibidas",
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
    """Agrega sesiones de disponibilidad (una por fecha) para el técnico logueado."""
    tecnico = _tecnico_actual()
    if not tecnico:
        flash("Tu cuenta no está vinculada a un técnico.", "error")
        return _volver_disponibilidad()
    tecnico_id = tecnico["id"]

    fechas = [f.strip() for f in request.form.getlist("fechas") if (f or "").strip()]
    if not fechas:
        una = (request.form.get("fecha") or "").strip()
        fechas = [una] if una else []
    hora_inicio = (request.form.get("hora_inicio") or "").strip()
    hora_fin = (request.form.get("hora_fin") or "").strip()
    # El formulario manda un "false" oculto + el checkbox ("true") cuando está marcado.
    valores = request.form.getlist("disponible")
    disponible = ("true" in valores) or not valores

    if not fechas:
        flash("Indica la fecha de la sesión.", "error")
        return _volver_disponibilidad()
    ahora = agenda_service.ahora_lima()
    for f in fechas:  # se valida todo antes de guardar nada
        error = agenda_service.validar_franja(f, hora_inicio, hora_fin, ahora)
        if error:
            flash(error, "error")
            return _volver_disponibilidad()

    agregadas = 0
    try:
        db = get_admin_client()
        for f in fechas:
            if _hay_traslape(tecnico_id, f, hora_inicio, hora_fin):
                flash(f"Ya existe una sesión que se cruza con ese horario el {f}.", "error")
                break
            db.table("agenda_tecnicos").insert({
                "tecnico_id": tecnico_id,
                "fecha": f,
                "hora_inicio": hora_inicio,
                "hora_fin": hora_fin,
                "disponible": bool(disponible),
            }).execute()
            agregadas += 1
    except Exception as exc:  # noqa: BLE001
        log.exception("No se pudo agregar franja de agenda")
        flash(agenda_service.mensaje_error_bd(exc, "No se pudo agregar la disponibilidad."), "error")
        return _volver_disponibilidad()
    if agregadas:
        flash("Disponibilidad agregada." if agregadas == 1 else f"Se agregaron {agregadas} sesiones.", "success")
    return _volver_disponibilidad()


def _volver_disponibilidad():
    return redirect(url_for("worker.disponibilidad_modulo"))


def _hay_traslape(tecnico_id, fecha: str, hora_inicio: str, hora_fin: str, excluir_id=None) -> bool:
    """True si el técnico ya tiene otra sesión vigente que se cruza con ese horario."""
    existentes = (
        get_reader()
        .table("agenda_tecnicos")
        .select("id,hora_inicio,hora_fin,estado")
        .eq("tecnico_id", tecnico_id)
        .eq("fecha", fecha)
        .execute()
        .data
        or []
    )
    for ex in existentes:
        if excluir_id is not None and ex.get("id") == excluir_id:
            continue
        if ex.get("estado") == agenda_service.ESTADO_VENCIDA:
            continue
        hi = (ex.get("hora_inicio") or "")[:5]
        hf = (ex.get("hora_fin") or "")[:5]
        if hi and hf and not (hf <= hora_inicio or hora_fin <= hi):
            return True
    return False


def _leer_franja(franja_id, tecnico_id):
    filas = (
        get_reader()
        .table("agenda_tecnicos")
        .select("id,tecnico_id,fecha,hora_inicio,hora_fin,disponible,estado")
        .eq("id", franja_id)
        .eq("tecnico_id", tecnico_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return filas[0] if filas else None


def _franja_vencida(f: dict) -> bool:
    return agenda_service.estado_efectivo(
        (f.get("fecha") or "")[:10], f.get("hora_fin") or "", f.get("disponible"), f.get("estado")
    ) == agenda_service.ESTADO_VENCIDA


def _citas_activas_en_franja(tecnico_id, f: dict) -> list:
    """Citas abiertas del técnico (hora de inicio) que caen dentro de la sesión indicada."""
    reservas = (
        get_reader()
        .table("reservas")
        .select("id,hora_inicio,estado")
        .eq("tecnico_id", tecnico_id)
        .eq("fecha_reserva", (f.get("fecha") or "")[:10])
        .in_("estado", ["CONFIRMADA", "ASIGNADA", "EN_ATENCION"])
        .execute()
        .data
        or []
    )
    hi_f = (f.get("hora_inicio") or "")[:5]
    hf_f = (f.get("hora_fin") or "")[:5]
    return [(r.get("hora_inicio") or "")[:5] for r in reservas if hi_f <= (r.get("hora_inicio") or "")[:5] < hf_f]


@bp.post("/worker/agenda/<int:franja_id>/editar")
@roles_required("worker")
@tecnico_required
def agenda_editar(franja_id):
    """Cambia fecha y horas de una sesión vigente (nunca a una fecha/hora pasada)."""
    tecnico = _tecnico_actual()
    if not tecnico:
        flash("Tu cuenta no está vinculada a un técnico.", "error")
        return _volver_disponibilidad()
    fecha = (request.form.get("fecha") or "").strip()
    hora_inicio = (request.form.get("hora_inicio") or "").strip()
    hora_fin = (request.form.get("hora_fin") or "").strip()
    try:
        franja = _leer_franja(franja_id, tecnico["id"])
        if not franja:
            flash("Sesión no encontrada.", "warning")
            return _volver_disponibilidad()
        if _franja_vencida(franja):
            flash("La sesión ya venció y no se puede editar; crea una nueva.", "warning")
            return _volver_disponibilidad()
        error = agenda_service.validar_franja(fecha, hora_inicio, hora_fin)
        if error:
            flash(error, "error")
            return _volver_disponibilidad()
        if _hay_traslape(tecnico["id"], fecha, hora_inicio, hora_fin, excluir_id=franja_id):
            flash("Ya tienes otra sesión que se cruza con ese horario.", "error")
            return _volver_disponibilidad()
        # No dejar fuera una cita ya asignada que estaba dentro de la sesión original.
        for hora_cita in _citas_activas_en_franja(tecnico["id"], franja):
            sigue_dentro = fecha == (franja.get("fecha") or "")[:10] and hora_inicio <= hora_cita < hora_fin
            if not sigue_dentro:
                flash(
                    f"Tienes una cita a las {hora_cita} dentro de esta sesión; el nuevo horario la dejaría fuera.",
                    "error",
                )
                return _volver_disponibilidad()
        get_admin_client().table("agenda_tecnicos").update({
            "fecha": fecha, "hora_inicio": hora_inicio, "hora_fin": hora_fin,
        }).eq("id", franja_id).eq("tecnico_id", tecnico["id"]).execute()
        flash("Sesión actualizada.", "success")
    except Exception as exc:  # noqa: BLE001
        log.exception("No se pudo editar la franja %s", franja_id)
        flash(agenda_service.mensaje_error_bd(exc, "No se pudo actualizar la sesión."), "error")
    return _volver_disponibilidad()


@bp.post("/worker/agenda/<int:franja_id>/quitar")
@roles_required("worker")
@tecnico_required
def agenda_quitar(franja_id):
    """Elimina una sesión vigente del técnico logueado (las vencidas se conservan como historial)."""
    tecnico = _tecnico_actual()
    if not tecnico:
        flash("Tu cuenta no está vinculada a un técnico.", "error")
        return _volver_disponibilidad()
    try:
        f = _leer_franja(franja_id, tecnico["id"])
        if not f:
            flash("Sesión no encontrada.", "warning")
            return _volver_disponibilidad()
        if _franja_vencida(f):
            flash("Las sesiones vencidas se conservan como historial y no se eliminan.", "warning")
            return _volver_disponibilidad()
        # No quitar una sesión que tiene una cita CONFIRMADA/ASIGNADA/EN_ATENCION dentro.
        if _citas_activas_en_franja(tecnico["id"], f):
            flash("No se puede eliminar una sesión que tiene una cita CONFIRMADA o EN_ATENCION.", "error")
            return _volver_disponibilidad()
        resp_del = get_admin_client().table("agenda_tecnicos").delete().eq("id", franja_id).eq("tecnico_id", tecnico["id"]).execute()
        if not getattr(resp_del, "data", None):
            flash("Sesión no encontrada.", "warning")
            return _volver_disponibilidad()
        flash("Sesión de disponibilidad eliminada.", "success")
    except Exception:  # noqa: BLE001
        log.exception("No se pudo eliminar la franja")
        flash("No se pudo eliminar la sesión.", "error")
    return _volver_disponibilidad()


@bp.post("/worker/agenda/<int:franja_id>/toggle")
@roles_required("worker")
@tecnico_required
def agenda_toggle(franja_id):
    tecnico = _tecnico_actual()
    if not tecnico:
        flash("Tu cuenta no está vinculada a un técnico.", "error")
        return _volver_disponibilidad()
    try:
        f = _leer_franja(franja_id, tecnico["id"])
        if not f:
            flash("Sesión no encontrada.", "warning")
            return _volver_disponibilidad()
        if _franja_vencida(f):
            flash("La sesión ya venció y no se puede activar ni desactivar.", "warning")
            return _volver_disponibilidad()
        nuevo = not bool(f.get("disponible"))
        get_admin_client().table("agenda_tecnicos").update({"disponible": nuevo}).eq("id", franja_id).eq("tecnico_id", tecnico["id"]).execute()
        flash("Disponibilidad actualizada.", "success")
    except Exception as exc:  # noqa: BLE001
        log.exception("No se pudo actualizar la franja")
        flash(agenda_service.mensaje_error_bd(exc, "No se pudo actualizar la sesión."), "error")
    return _volver_disponibilidad()


# ---------------------------------------------------------------------------
# Citas: tomar una cita libre, avanzar su estado y abrir la reparación
# ---------------------------------------------------------------------------

def _volver_panel(hash_tab: str = ""):
    return redirect(url_for("worker.dashboard") + hash_tab)


def _cargar_cita(cita_id):
    """Cita (reserva) por id; None si no existe o falló la lectura."""
    try:
        filas = (
            get_reader()
            .table("reservas")
            .select("id,estado,tecnico_id,fecha_reserva,hora_inicio,electrodomestico_id,tipo_atencion,motivo,cliente_id")
            .eq("id", cita_id)
            .limit(1)
            .execute()
        ).data or []
        return filas[0] if filas else None
    except Exception:  # noqa: BLE001
        log.exception("No se pudo leer la cita %s", cita_id)
        return None


@bp.post("/worker/citas/<int:cita_id>/tomar")
@roles_required("worker")
@tecnico_required
def cita_tomar(cita_id):
    """El técnico se asigna una cita PENDIENTE que nadie tiene aún."""
    tecnico_id = _tecnico_id()
    if not tecnico_id:
        flash("Tu cuenta no está vinculada a un técnico.", "error")
        return _volver_panel()
    cita = _cargar_cita(cita_id)
    if not cita:
        flash("Cita no encontrada.", "warning")
        return _volver_panel()
    if cita.get("tecnico_id"):
        flash("Esa cita ya tiene un técnico asignado.", "warning")
        return _volver_panel()
    if cita.get("estado") != "PENDIENTE":
        flash("Solo se pueden tomar citas pendientes.", "warning")
        return _volver_panel()
    fecha = (cita.get("fecha_reserva") or "")[:10]
    ahora = agenda_service.ahora_lima()
    hora_cita = (cita.get("hora_inicio") or "")[:5]
    if fecha < ahora.date().isoformat() or (fecha == ahora.date().isoformat() and hora_cita < ahora.strftime("%H:%M")):
        flash("La cita ya venció; pídele al administrador que la reprograme o la cierre.", "warning")
        return _volver_panel()

    # Si el técnico marcó su agenda ese día, la hora de la cita debe caer en una franja disponible.
    try:
        franjas = (
            get_reader()
            .table("agenda_tecnicos")
            .select("fecha,hora_inicio,hora_fin,disponible,estado")
            .eq("tecnico_id", tecnico_id)
            .eq("fecha", fecha)
            .execute()
        ).data or []
    except Exception:  # noqa: BLE001
        franjas = []
    hora = hora_cita
    if franjas and not any(agenda_service.franja_vigente(f, ahora) and _cubre_franja(f, hora) for f in franjas):
        flash("Esa hora está fuera de tu disponibilidad registrada para ese día.", "warning")
        return _volver_panel()

    try:
        resp = (
            get_admin_client()
            .table("reservas")
            .update({"tecnico_id": tecnico_id, "estado": "CONFIRMADA"})
            .eq("id", cita_id)
            .is_("tecnico_id", "null")
            .eq("estado", "PENDIENTE")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        if _es_unico_duplicado(exc):
            flash("Ya tienes otra cita a esa misma hora.", "error")
        else:
            log.exception("No se pudo tomar la cita %s", cita_id)
            flash("No se pudo tomar la cita. Inténtalo nuevamente.", "error")
        return _volver_panel()
    if not resp.data:
        flash("Otro técnico tomó esa cita antes que tú.", "warning")
        return _volver_panel()
    marcar_primera_atencion(cita_id)
    flash("Cita asignada a ti y confirmada.", "success")
    return _volver_panel()


@bp.post("/worker/citas/<int:cita_id>/estado")
@roles_required("worker")
@tecnico_required
def cita_cambiar_estado(cita_id):
    """Avanza el estado de una cita propia según TRANSICIONES_CITA."""
    tecnico_id = _tecnico_id()
    cita = _cargar_cita(cita_id)
    if not cita or not tecnico_id or cita.get("tecnico_id") != tecnico_id:
        flash("Cita no encontrada.", "warning")
        return _volver_panel()
    nuevo = (request.form.get("estado") or "").strip().upper()
    actual = cita.get("estado") or "PENDIENTE"
    if nuevo not in TRANSICIONES_CITA.get(actual, set()):
        flash(
            f"No se puede pasar la cita de {ESTADO_CITA_LABEL.get(actual, actual)} "
            f"a {ESTADO_CITA_LABEL.get(nuevo, nuevo)}.",
            "error",
        )
        return _volver_panel()
    try:
        resp = (
            get_admin_client()
            .table("reservas")
            .update({"estado": nuevo})
            .eq("id", cita_id)
            .eq("tecnico_id", tecnico_id)
            .eq("estado", actual)
            .execute()
        )
    except Exception:  # noqa: BLE001
        log.exception("No se pudo cambiar el estado de la cita %s", cita_id)
        flash("No se pudo actualizar la cita. Inténtalo nuevamente.", "error")
        return _volver_panel()
    if not resp.data:
        flash("La cita cambió mientras la editabas. Revisa su estado actual.", "warning")
        return _volver_panel()
    if nuevo == "EN_ATENCION":
        marcar_primera_atencion(cita_id)
    flash(f"Cita: {ESTADO_CITA_LABEL.get(nuevo, nuevo)}.", "success")
    return _volver_panel()


def _registrar_historial(reparacion_id, anterior, nuevo, comentario=None) -> None:
    """Deja constancia del cambio de estado en `historial_reparacion` (no rompe el flujo)."""
    try:
        get_admin_client().table("historial_reparacion").insert({
            "reparacion_id": reparacion_id,
            "usuario_id": session.get("usuario_id"),
            "estado_anterior": anterior,
            "estado_nuevo": nuevo,
            "comentario": comentario,
        }).execute()
    except Exception:  # noqa: BLE001
        log.exception("No se pudo guardar el historial de la reparación %s", reparacion_id)


@bp.post("/worker/citas/<int:cita_id>/reparacion")
@roles_required("worker")
@tecnico_required
def cita_crear_reparacion(cita_id):
    """Abre la orden de servicio (reparación) de una cita propia en atención."""
    tecnico_id = _tecnico_id()
    cita = _cargar_cita(cita_id)
    if not cita or not tecnico_id or cita.get("tecnico_id") != tecnico_id:
        flash("Cita no encontrada.", "warning")
        return _volver_panel()
    if cita.get("estado") not in ("EN_ATENCION", "ATENDIDA"):
        flash("La reparación se crea cuando la cita está en atención.", "warning")
        return _volver_panel()
    if not cita.get("electrodomestico_id"):
        flash("La cita no tiene un equipo registrado; no se puede crear la reparación.", "error")
        return _volver_panel()

    try:
        existente = (
            get_reader().table("reparaciones").select("id")
            .eq("reserva_id", cita_id).limit(1).execute()
        ).data or []
    except Exception:  # noqa: BLE001
        existente = []
    if existente:
        flash("Esa cita ya tiene una reparación creada.", "info")
        return redirect(url_for("worker.reparacion_detalle", reparacion_id=existente[0]["id"]))

    try:
        resp = get_admin_client().table("reparaciones").insert({
            "electrodomestico_id": cita["electrodomestico_id"],
            "reserva_id": cita_id,
            "usuario_registro_id": session.get("usuario_id"),
            "tecnico_id": tecnico_id,
            "tipo_atencion": cita.get("tipo_atencion") or "TALLER",
            "estado": "RECIBIDO",
            "problema": (cita.get("motivo") or "").strip() or "Por diagnosticar",
        }).execute()
        reparacion_id = (resp.data or [{}])[0].get("id")
    except Exception as exc:  # noqa: BLE001
        if _es_unico_duplicado(exc):
            flash("Esa cita ya tiene una reparación creada.", "info")
        else:
            log.exception("No se pudo crear la reparación de la cita %s", cita_id)
            flash("No se pudo crear la reparación. Inténtalo nuevamente.", "error")
        return _volver_panel()
    if not reparacion_id:
        flash("No se pudo crear la reparación.", "error")
        return _volver_panel()
    _registrar_historial(reparacion_id, None, "RECIBIDO", f"Reparación creada desde la cita #{cita_id}")
    flash(f"Reparación OS-{reparacion_id} creada.", "success")
    return redirect(url_for("worker.reparacion_detalle", reparacion_id=reparacion_id))


# ---------------------------------------------------------------------------
# Reparaciones: detalle, estado, diagnóstico, fotos y PDF
# ---------------------------------------------------------------------------

def _cargar_reparacion(reparacion_id):
    """Reparación si el usuario puede gestionarla (su técnico o un supervisor); si no, None."""
    try:
        filas = (
            get_reader().table("reparaciones").select("*")
            .eq("id", reparacion_id).limit(1).execute()
        ).data or []
    except Exception:  # noqa: BLE001
        log.exception("No se pudo leer la reparación %s", reparacion_id)
        return None
    if not filas:
        return None
    rep = filas[0]
    if session.get("rol") == "SUPERVISOR":
        return rep
    tecnico_id = _tecnico_id()
    if tecnico_id and rep.get("tecnico_id") == tecnico_id:
        return rep
    return None


def _volver_reparacion(reparacion_id):
    return redirect(url_for("worker.reparacion_detalle", reparacion_id=reparacion_id))


def _datos_equipo_cliente(electrodomestico_id) -> tuple:
    """(equipo, cliente) de una reparación; dicts vacíos si no se pueden leer."""
    if not electrodomestico_id:
        return {}, {}
    try:
        filas = (
            get_reader()
            .table("electrodomesticos")
            .select(
                "id,modelo,numero_serie,tipo_otro,marca_otro,marcas(nombre),tipos_electrodomestico(nombre),"
                "clientes(nombres,apellidos,razon_social,telefono,numero_documento)"
            )
            .eq("id", electrodomestico_id)
            .limit(1)
            .execute()
        ).data or []
    except Exception:  # noqa: BLE001
        log.exception("No se pudo leer el equipo %s", electrodomestico_id)
        return {}, {}
    if not filas:
        return {}, {}
    e = filas[0]
    cliente = e.get("clientes") or {}
    equipo = {
        "modelo": e.get("modelo"),
        "numero_serie": e.get("numero_serie"),
        "descripcion": nombre_equipo(e),
    }
    return equipo, cliente


@bp.get("/worker/reparaciones/<int:reparacion_id>")
@roles_required("worker")
def reparacion_detalle(reparacion_id):
    rep = _cargar_reparacion(reparacion_id)
    if not rep:
        flash("Reparación no encontrada.", "warning")
        return _volver_panel()
    equipo, cliente = _datos_equipo_cliente(rep.get("electrodomestico_id"))

    fotos, historial = [], []
    try:
        filas = (
            get_reader().table("evidencias_reparacion")
            .select("id,url_archivo,descripcion")
            .eq("reparacion_id", reparacion_id).eq("tipo", "FOTO")
            .order("fecha_creacion", desc=True).execute()
        ).data or []
        fotos = [{"url": f["url_archivo"], "descripcion": f.get("descripcion")} for f in filas if f.get("url_archivo")]
    except Exception:  # noqa: BLE001
        log.exception("No se pudieron cargar las fotos de la reparación %s", reparacion_id)
    try:
        historial = (
            get_reader().table("historial_reparacion")
            .select("estado_anterior,estado_nuevo,comentario,fecha")
            .eq("reparacion_id", reparacion_id)
            .order("fecha", desc=True).limit(30).execute()
        ).data or []
    except Exception:  # noqa: BLE001
        log.exception("No se pudo cargar el historial de la reparación %s", reparacion_id)

    permitidas = TRANSICIONES_REPARACION.get(rep.get("estado") or "", set())
    return render_template(
        "worker/reparacion_detalle.html",
        reparacion=rep,
        cliente=cliente,
        equipo=equipo,
        estados_labels=ESTADOS_LABEL,
        transiciones=[e for e in ESTADOS_LABEL if e in permitidas],
        fotos=fotos,
        historial=historial,
        max_fotos=MAX_FOTOS_REPARACION,
    )


@bp.post("/worker/reparaciones/<int:reparacion_id>/estado")
@roles_required("worker")
def reparacion_cambiar_estado(reparacion_id):
    rep = _cargar_reparacion(reparacion_id)
    if not rep:
        flash("Reparación no encontrada.", "warning")
        return _volver_panel()
    nuevo = (request.form.get("estado") or "").strip().upper()
    comentario = (request.form.get("comentario") or "").strip()[:300] or None
    actual = rep.get("estado") or "RECIBIDO"
    if nuevo not in TRANSICIONES_REPARACION.get(actual, set()):
        flash(
            f"No se puede pasar de {ESTADOS_LABEL.get(actual, actual)} a {ESTADOS_LABEL.get(nuevo, nuevo)}.",
            "error",
        )
        return _volver_reparacion(reparacion_id)

    cambios = {"estado": nuevo}
    ahora = _ahora_iso()
    if nuevo == "EN_REPARACION" and not rep.get("fecha_inicio"):
        cambios["fecha_inicio"] = ahora
    if nuevo == "REPARADO":
        cambios["fecha_fin"] = ahora
    if nuevo == "ENTREGADO":
        cambios["fecha_entrega"] = ahora

    try:
        resp = (
            get_admin_client().table("reparaciones").update(cambios)
            .eq("id", reparacion_id).eq("estado", actual).execute()
        )
    except Exception:  # noqa: BLE001
        log.exception("No se pudo cambiar el estado de la reparación %s", reparacion_id)
        flash("No se pudo actualizar el estado. Inténtalo nuevamente.", "error")
        return _volver_reparacion(reparacion_id)
    if not resp.data:
        flash("La reparación cambió mientras la editabas. Revisa su estado actual.", "warning")
        return _volver_reparacion(reparacion_id)

    _registrar_historial(reparacion_id, actual, nuevo, comentario)
    equipo_estado = ESTADO_EQUIPO.get(nuevo)
    if equipo_estado and rep.get("electrodomestico_id"):
        try:
            get_admin_client().table("electrodomesticos").update(
                {"estado": equipo_estado}
            ).eq("id", rep["electrodomestico_id"]).execute()
        except Exception:  # noqa: BLE001
            log.exception("No se pudo sincronizar el estado del equipo %s", rep["electrodomestico_id"])
    flash(f"Estado actualizado: {ESTADOS_LABEL.get(nuevo, nuevo)}.", "success")
    return _volver_reparacion(reparacion_id)


@bp.post("/worker/reparaciones/<int:reparacion_id>/diagnostico")
@roles_required("worker")
def reparacion_guardar_diagnostico(reparacion_id):
    rep = _cargar_reparacion(reparacion_id)
    if not rep:
        flash("Reparación no encontrada.", "warning")
        return _volver_panel()
    diagnostico = (request.form.get("diagnostico") or "").strip()
    notas = (request.form.get("notas_tecnico") or "").strip()
    if len(diagnostico) > 2000 or len(notas) > 2000:
        flash("El diagnóstico y las notas admiten hasta 2000 caracteres cada uno.", "error")
        return _volver_reparacion(reparacion_id)
    costo_txt = (request.form.get("costo_estimado") or "").strip().replace(",", ".")
    costo = None
    if costo_txt:
        try:
            costo = round(float(costo_txt), 2)
        except ValueError:
            costo = -1
        if costo < 0 or costo > 1_000_000:
            flash("El costo estimado debe ser un monto válido (0 o mayor).", "error")
            return _volver_reparacion(reparacion_id)
    try:
        get_admin_client().table("reparaciones").update({
            "diagnostico": diagnostico or None,
            "notas_tecnico": notas or None,
            "costo_estimado": costo,
        }).eq("id", reparacion_id).execute()
    except Exception:  # noqa: BLE001
        log.exception("No se pudo guardar el diagnóstico de la reparación %s", reparacion_id)
        flash("No se pudo guardar. Inténtalo nuevamente.", "error")
        return _volver_reparacion(reparacion_id)
    flash("Diagnóstico y notas guardados.", "success")
    return _volver_reparacion(reparacion_id)


@bp.post("/worker/reparaciones/<int:reparacion_id>/foto")
@roles_required("worker")
def reparacion_subir_foto(reparacion_id):
    rep = _cargar_reparacion(reparacion_id)
    if not rep:
        flash("Reparación no encontrada.", "warning")
        return _volver_panel()
    try:
        total = (
            get_reader().table("evidencias_reparacion").select("id", count="exact")
            .eq("reparacion_id", reparacion_id).limit(1).execute()
        ).count or 0
    except Exception:  # noqa: BLE001
        total = 0
    if total >= MAX_FOTOS_REPARACION:
        flash(f"Una reparación admite hasta {MAX_FOTOS_REPARACION} fotos.", "error")
        return _volver_reparacion(reparacion_id)
    try:
        url, _ruta = storage_service.subir_imagen(
            storage_service.BUCKET_EVIDENCIAS,
            f"reparacion-{reparacion_id}/{uuid.uuid4().hex[:12]}",
            request.files.get("foto"),
        )
    except ValidationError as exc:
        flash(exc.message, "error")
        return _volver_reparacion(reparacion_id)
    except Exception:  # noqa: BLE001
        log.exception("No se pudo subir la foto de la reparación %s", reparacion_id)
        flash("No se pudo subir la foto. Inténtalo nuevamente.", "error")
        return _volver_reparacion(reparacion_id)
    try:
        get_admin_client().table("evidencias_reparacion").insert({
            "reparacion_id": reparacion_id,
            "usuario_id": session.get("usuario_id"),
            "tipo": "FOTO",
            "url_archivo": url,
        }).execute()
    except Exception:  # noqa: BLE001
        log.exception("No se pudo registrar la evidencia de la reparación %s", reparacion_id)
        flash("La foto se subió, pero no se pudo registrar. Inténtalo nuevamente.", "error")
        return _volver_reparacion(reparacion_id)
    flash("Foto agregada.", "success")
    return _volver_reparacion(reparacion_id)


@bp.get("/worker/reparaciones/<int:reparacion_id>/pdf")
@roles_required("worker")
def reparacion_pdf(reparacion_id):
    rep = _cargar_reparacion(reparacion_id)
    if not rep:
        flash("Reparación no encontrada.", "warning")
        return _volver_panel()
    equipo, cliente = _datos_equipo_cliente(rep.get("electrodomestico_id"))

    direccion = ""
    if rep.get("reserva_id") and rep.get("tipo_atencion") == "DOMICILIO":
        try:
            r = (
                get_reader().table("reservas").select("calle,distrito")
                .eq("id", rep["reserva_id"]).limit(1).execute()
            ).data or [{}]
            partes = [(r[0].get("distrito") or "").strip(), (r[0].get("calle") or "").strip()]
            direccion = " - ".join(dict.fromkeys(p for p in partes if p))
        except Exception:  # noqa: BLE001
            log.exception("No se pudo leer la dirección de la reparación %s", reparacion_id)

    tecnico_nombre = ""
    if rep.get("tecnico_id"):
        try:
            t = (
                get_reader().table("tecnicos").select("empleados(nombres,apellidos)")
                .eq("id", rep["tecnico_id"]).limit(1).execute()
            ).data or [{}]
            emp = t[0].get("empleados") or {}
            tecnico_nombre = " ".join(filter(None, [emp.get("nombres"), emp.get("apellidos")]))
        except Exception:  # noqa: BLE001
            log.exception("No se pudo leer el técnico de la reparación %s", reparacion_id)

    try:
        pdf = generar_orden_servicio_pdf({
            "codigo": f"OS-{rep['id']}",
            "fecha_ingreso": rep.get("fecha_ingreso"),
            "estado": ESTADOS_LABEL.get(rep.get("estado") or "", rep.get("estado")),
            "tipo_atencion": rep.get("tipo_atencion"),
            "cliente_nombre": cliente.get("razon_social")
            or " ".join(filter(None, [cliente.get("nombres"), cliente.get("apellidos")])),
            "cliente_telefono": cliente.get("telefono"),
            "cliente_documento": cliente.get("numero_documento"),
            "direccion": direccion,
            "equipo": equipo.get("descripcion"),
            "numero_serie": equipo.get("numero_serie"),
            "problema": rep.get("problema"),
            "diagnostico": rep.get("diagnostico"),
            "notas": rep.get("notas_tecnico"),
            "costo_estimado": rep.get("costo_estimado"),
            "tecnico_nombre": tecnico_nombre,
        })
    except Exception:  # noqa: BLE001
        log.exception("No se pudo generar el PDF de la reparación %s", reparacion_id)
        flash("No se pudo generar el PDF.", "error")
        return _volver_reparacion(reparacion_id)
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="OS-{reparacion_id}.pdf"'},
    )
