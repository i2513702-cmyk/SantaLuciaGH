"""Endpoints del panel del personal técnico E67–E78."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote_plus

from flask import jsonify, request

from app.api import bp
from app.api.common import (
    ApiError,
    api_auth,
    api_route,
    appointments_payload,
    arg_entero,
    booleano,
    cita_por_id,
    contexto_actual,
    cuerpo,
    datetime_iso,
    decimal,
    delete_row,
    dinero,
    entero,
    fecha_iso,
    hoy_lima,
    insert_row,
    meta_guardar,
    meta_obtener,
    mover_stock,
    notificar,
    now_utc_iso,
    paginacion,
    producto_por_id,
    read_one,
    read_rows,
    requerir,
    sumar_hora,
    texto,
    update_row,
    usuario_actual,
    usuario_por_cliente,
    validar_fecha,
    validar_hora,
)
from app.services import storage_service

ESTADOS_CITA = {
    "PENDIENTE",
    "ASIGNADA",
    "EN_CAMINO",
    "EN_DIAGNOSTICO",
    "REQUIERE_REPUESTO",
    "EN_REVISION_TALLER",
    "COMPLETADA",
    "CANCELADA",
}
ESTADOS_TECNICO = {"EN_RUTA", "ATENDIENDO", "PAUSA"}
ROLES_STAFF = {
    "ADMINISTRADOR",
    "SUPERVISOR",
    "TECNICO",
    "RECEPCIONISTA",
    "VENDEDOR",
    "ALMACENERO",
}
ROLES_VER_TODAS = {"ADMINISTRADOR", "SUPERVISOR"}


def _es_superior() -> bool:
    return contexto_actual().get("rol") in ROLES_VER_TODAS


def _tecnico_actual() -> dict | None:
    usuario = usuario_actual()
    if not usuario.get("empleado_id"):
        return None
    tecnico = read_one("tecnicos", "*", empleado_id=usuario.get("empleado_id"))
    if tecnico:
        return tecnico
    try:
        tecnico = insert_row(
            "tecnicos",
            {"empleado_id": usuario.get("empleado_id"), "especialidad": "GENERAL", "nivel": "JUNIOR"},
        )
        return tecnico
    except Exception:  # noqa: BLE001
        return None


def _tecnico_id_actual() -> int:
    tecnico = _tecnico_actual()
    return int(tecnico.get("id")) if tecnico else 0


def _nombre_tecnico(tecnico: dict | None) -> str:
    if not tecnico:
        return ""
    empleado = read_one("empleados", "nombres,apellidos", id=tecnico.get("empleado_id")) or {}
    return " ".join(x for x in (empleado.get("nombres"), empleado.get("apellidos")) if x).strip()


def _esta_asignada(reserva: dict) -> bool:
    return bool(_tecnico_id_actual()) and int(reserva.get("tecnico_id") or 0) == _tecnico_id_actual()


def _puede_gestionar_cita(reserva: dict) -> bool:
    rol = contexto_actual().get("rol")
    if rol in ROLES_VER_TODAS:
        return True
    if rol == "TECNICO":
        return _esta_asignada(reserva)
    return False


def _fecha_arg(valor: str | None, defecto_hoy: bool = True) -> date | None:
    valor = texto(valor).lower()
    if not valor or valor == "hoy":
        return hoy_lima() if defecto_hoy else None
    try:
        return date.fromisoformat(valor)
    except ValueError as exc:
        raise ApiError("La fecha no es válida (YYYY-MM-DD).", 400, "DATOS_INVALIDOS") from exc


def _turno(inicio: str) -> str:
    return "MAÑANA" if str(inicio)[:5] < "13:00" else "TARDE"


def _franja_payload(fila: dict) -> dict:
    inicio = str(fila.get("hora_inicio") or "")[:5]
    return {
        "id": fila.get("id"),
        "fecha": fecha_iso(fila.get("fecha")),
        "turno": _turno(inicio),
        "hora_inicio": inicio,
        "hora_fin": str(fila.get("hora_fin") or "")[:5],
        "activo": bool(fila.get("disponible")) and (fila.get("estado") or "").upper() != "VENCIDA",
    }


def _enlace_mapa(item: dict) -> str:
    meta = item.get("_meta") or {}
    lat = meta.get("latitud")
    lng = meta.get("longitud")
    if lat not in (None, "") and lng not in (None, ""):
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    direccion = item.get("direccion") or "Santa Lucía Huancayo"
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(str(direccion))}"


def _cita_staff(item: dict) -> dict:
    item["telefono_cliente"] = (item.get("cliente") or {}).get("telefono") or ""
    item["enlace_mapa"] = _enlace_mapa(item)
    item["fase_actual"] = item.get("estado") or "PENDIENTE"
    item.pop("_meta", None)
    return item


def _van_items(tecnico_id: int) -> list[dict]:
    meta = meta_obtener("VAN_STOCK", tecnico_id) or {}
    crudos = meta.get("items") if isinstance(meta, dict) else None
    if not isinstance(crudos, list):
        return []
    acumulado = {}
    for fila in crudos:
        if not isinstance(fila, dict):
            continue
        pid = entero(fila.get("producto_id"))
        cantidad = entero(fila.get("cantidad"))
        if pid <= 0 or cantidad <= 0:
            continue
        acumulado[pid] = acumulado.get(pid, 0) + cantidad
    items = []
    for pid, cantidad in acumulado.items():
        p = producto_por_id(pid)
        if not p:
            continue
        items.append(
            {
                "producto_id": pid,
                "sku": p.get("codigo") or f"PRO-{pid}",
                "nombre": p.get("nombre") or "",
                "cantidad": cantidad,
            }
        )
    return items


@bp.get("/staff/dashboard")
@api_route
@api_auth(*ROLES_STAFF)
def staff_dashboard():
    usuario = usuario_actual()
    ctx = contexto_actual()
    hoy = hoy_lima()
    tecnico = _tecnico_actual()
    tecnico_id = int(tecnico.get("id")) if tecnico else 0
    reservas = read_rows("reservas", "*", fecha_reserva=hoy.isoformat(), order="hora_inicio")
    if tecnico_id:
        reservas = [r for r in reservas if int(r.get("tecnico_id") or 0) == tecnico_id]
    completadas = sum(1 for r in reservas if (r.get("estado") or "").upper() == "COMPLETADA")
    pendientes = sum(1 for r in reservas if (r.get("estado") or "").upper() not in {"COMPLETADA", "CANCELADA"})
    ahora = datetime.now().strftime("%H:%M")
    proximas = [r for r in reservas if str(r.get("hora_inicio") or "")[:5] >= ahora and (r.get("estado") or "").upper() != "CANCELADA"]
    proxima = None
    if proximas:
        siguiente = proximas[0]
        try:
            hh, mm = map(int, str(siguiente.get("hora_inicio"))[:5].split(":"))
            mins = max(0, (hh * 60 + mm) - (int(ahora[:2]) * 60 + int(ahora[3:5])))
        except (TypeError, ValueError):
            mins = 0
        proxima = {"id": siguiente.get("id"), "hora": str(siguiente.get("hora_inicio"))[:5], "en_minutos": mins}
    nombre = " ".join(
        x
        for x in (
            (read_one("empleados", "nombres,apellidos", id=usuario.get("empleado_id")) or {}).get("nombres")
            if usuario.get("empleado_id")
            else "",
            (read_one("empleados", "nombres,apellidos", id=usuario.get("empleado_id")) or {}).get("apellidos")
            if usuario.get("empleado_id")
            else "",
        )
        if x
    ).strip()
    estado = (meta_obtener("STAFF_ESTADO", tecnico_id) or {}).get("estado") if tecnico_id else ""
    foto = None
    try:
        foto = storage_service.obtener_avatar_url(usuario.get("id"))
    except Exception:  # noqa: BLE001
        foto = None
    return jsonify(
        {
            "perfil": {
                "id": usuario.get("id"),
                "codigo": f"EMP-{int(usuario.get('empleado_id') or usuario.get('id') or 0):04d}",
                "nombres": nombre or usuario.get("nombre_usuario") or "",
                "rol": ctx.get("rol"),
                "nivel": (tecnico or {}).get("nivel") or "",
                "zona": (meta_obtener("STAFF_ZONA", tecnico_id) or {}).get("zona", "") if tecnico_id else "",
                "foto_url": foto,
                "estado_actual": estado or "DISPONIBLE",
            },
            "resumen": {"citas_hoy": len(reservas), "completadas": completadas, "pendientes": pendientes},
            "proxima_cita": proxima,
            "stock_camioneta": {"total_repuestos": sum(i["cantidad"] for i in _van_items(tecnico_id)) if tecnico_id else 0},
            "sincronizado_en": now_utc_iso(),
        }
    )


@bp.patch("/staff/me/status")
@api_route
@api_auth("TECNICO")
def staff_status():
    data = cuerpo()
    estado = texto(data.get("estado")).upper()
    if estado not in ESTADOS_TECNICO:
        raise ApiError("El estado debe ser EN_RUTA, ATENDIENDO o PAUSA.", 400, "DATOS_INVALIDOS")
    tecnico = _tecnico_actual()
    if not tecnico:
        raise ApiError("Tu usuario no tiene un técnico asociado.", 400, "DATOS_INVALIDOS")
    meta_guardar(
        "STAFF_ESTADO",
        tecnico.get("id"),
        {"estado": estado, "actualizado_en": now_utc_iso()},
        usuario_id=usuario_actual().get("id"),
    )
    return jsonify({"estado": estado, "actualizado_en": now_utc_iso()})


@bp.get("/staff/appointments")
@api_route
@api_auth("TECNICO", "SUPERVISOR", "ADMINISTRADOR")
def staff_appointments():
    fecha = _fecha_arg(request.args.get("fecha"))
    estado = texto(request.args.get("estado")).upper()
    reservas = read_rows("reservas", "*", fecha_reserva=fecha.isoformat(), order="hora_inicio")
    if not _es_superior():
        reservas = [r for r in reservas if _esta_asignada(r)]
    if estado:
        reservas = [r for r in reservas if (r.get("estado") or "").upper() == estado]
    items = appointments_payload(reservas)
    return jsonify({"items": [_cita_staff(i) for i in items], "fecha": fecha.isoformat()})


@bp.patch("/staff/appointments/<int:reserva_id>/status")
@api_route
@api_auth("TECNICO", "SUPERVISOR", "ADMINISTRADOR")
def staff_appointment_status(reserva_id: int):
    reserva = cita_por_id(reserva_id)
    if not reserva:
        raise ApiError("La cita no existe.", 404, "NO_ENCONTRADO")
    if not _puede_gestionar_cita(reserva):
        raise ApiError("No tienes permiso para actualizar esta cita.", 403, "SIN_PERMISO")
    data = cuerpo()
    estado = texto(data.get("estado")).upper()
    if estado not in ESTADOS_CITA:
        raise ApiError("El estado de la cita no es válido.", 400, "DATOS_INVALIDOS")
    nota = texto(data.get("nota"))
    db_estado = {
        "EN_CAMINO": "ASIGNADA",
        "EN_DIAGNOSTICO": "ASIGNADA",
        "REQUIERE_REPUESTO": "ASIGNADA",
        "EN_REVISION_TALLER": "ASIGNADA",
        "COMPLETADA": "ATENDIDA",
    }.get(estado, estado)
    if db_estado not in {"PENDIENTE", "CONFIRMADA", "ASIGNADA", "ATENDIDA", "CANCELADA", "NO_ASISTIO"}:
        db_estado = "ASIGNADA"
    actualizada = update_row(
        "reservas",
        {"estado": db_estado, "fecha_actualizacion": now_utc_iso()},
        id=reserva_id,
    )
    meta = meta_obtener("CITA_META", reserva_id) or {}
    meta["estado_api"] = estado
    bitacora = meta.get("bitacora") if isinstance(meta.get("bitacora"), list) else []
    bitacora.append(
        {
            "estado": estado,
            "nota": nota,
            "fecha": now_utc_iso(),
            "tecnico_id": _tecnico_id_actual() or None,
            "latitud": decimal(data.get("latitud"), None) if data.get("latitud") not in (None, "") else None,
            "longitud": decimal(data.get("longitud"), None) if data.get("longitud") not in (None, "") else None,
        }
    )
    meta["bitacora"] = bitacora[-50:]
    meta_guardar("CITA_META", reserva_id, meta, usuario_id=usuario_actual().get("id"))
    usuario = usuario_por_cliente(reserva.get("cliente_id"))
    if usuario:
        notificar(
            usuario_id=usuario.get("id"),
            cliente_id=reserva.get("cliente_id"),
            tipo="CITA_ESTADO",
            titulo="Actualización de tu cita",
            mensaje=f"Tu cita CIT-{reserva_id:04d} cambió a {estado}.",
            referencia=f"/appointments/{reserva_id}",
        )
    datos = appointments_payload([actualizada or reserva])[0]
    return jsonify(_cita_staff(datos))


@bp.get("/staff/repairs")
@api_route
@api_auth("TECNICO", "SUPERVISOR", "ADMINISTRADOR")
def staff_repairs():
    page, limit, _offset = paginacion(20, 50)
    estado = texto(request.args.get("estado")).upper()
    fecha = None
    if texto(request.args.get("fecha")):
        fecha = _fecha_arg(request.args.get("fecha"))
    reparaciones = read_rows("reparaciones", "*", order="id", desc=True)
    if not _es_superior():
        tecnico_id = _tecnico_id_actual()
        reparaciones = [r for r in reparaciones if int(r.get("tecnico_id") or 0) == tecnico_id]
    if estado:
        reparaciones = [r for r in reparaciones if (r.get("estado") or "").upper() == estado]
    if fecha:
        filtradas = []
        for r in reparaciones:
            for valor in (r.get("fecha_fin"), r.get("fecha_actualizacion"), r.get("fecha_creacion")):
                if not valor:
                    continue
                try:
                    d = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
                if d.date() == fecha:
                    filtradas.append(r)
                    break
        reparaciones = filtradas
    total = len(reparaciones)
    pagina = reparaciones[(page - 1) * limit : page * limit]
    electro_ids = [r.get("electrodomestico_id") for r in pagina]
    electro = {e.get("id"): e for e in read_rows("electrodomesticos", "*", order="id") if e.get("id") in electro_ids} if electro_ids else {}
    pagos = read_rows("pagos", "reparacion_id,metodo_pago_id,estado", order="id")
    metodos = {m.get("id"): m.get("nombre") for m in read_rows("metodos_pago", "id,nombre")}
    pago_por_rep = {}
    for p in pagos:
        if p.get("reparacion_id"):
            pago_por_rep[p.get("reparacion_id")] = metodos.get(p.get("metodo_pago_id")) or ""
    items = []
    for r in pagina:
        e = electro.get(r.get("electrodomestico_id")) or {}
        items.append(
            {
                "id": r.get("id"),
                "ticket": f"REP-{int(r.get('id') or 0):04d}",
                "equipo": " ".join(x for x in (e.get("modelo"), e.get("numero_serie")) if x) or "Equipo",
                "resumen": r.get("problema") or r.get("diagnostico") or "",
                "monto_cobrado": dinero(r.get("costo_total")),
                "metodo_pago": pago_por_rep.get(r.get("id"), ""),
                "calificacion": decimal((meta_obtener("REPARACION_CALIFICACION", r.get("id")) or {}).get("valor"), 0),
                "cerrada_en": datetime_iso(r.get("fecha_fin") or r.get("fecha_entrega")),
                "distrito": (read_one("reservas", "distrito", id=r.get("reserva_id")) or {}).get("distrito") or "",
            }
        )
    return jsonify({"items": items, "page": page, "limit": limit, "total": total})


@bp.post("/staff/repairs/<int:reparacion_id>/close")
@api_route
@api_auth("TECNICO", "SUPERVISOR")
def staff_repair_close(reparacion_id: int):
    reparacion = read_one("reparaciones", "*", id=reparacion_id)
    if not reparacion:
        raise ApiError("La reparación no existe.", 404, "NO_ENCONTRADO")
    if contexto_actual().get("rol") == "TECNICO" and int(reparacion.get("tecnico_id") or 0) != _tecnico_id_actual():
        raise ApiError("Esta reparación no está asignada a ti.", 403, "SIN_PERMISO")
    data = cuerpo()
    diagnostico = texto(data.get("diagnostico"))
    trabajo = texto(data.get("trabajo_realizado"))
    monto = decimal(data.get("monto_cobrado"))
    metodo_texto = texto(data.get("metodo_pago")).upper()
    if not diagnostico:
        raise ApiError("El diagnóstico es obligatorio.", 400, "DATOS_INVALIDOS")
    if not trabajo:
        raise ApiError("El trabajo realizado es obligatorio.", 400, "DATOS_INVALIDOS")
    if monto <= 0:
        raise ApiError("El monto cobrado debe ser mayor que cero.", 400, "DATOS_INVALIDOS")
    if metodo_texto not in {"EFECTIVO", "YAPE", "PLIN", "TARJETA"}:
        raise ApiError("El método de pago no es válido.", 400, "DATOS_INVALIDOS")
    metodo = read_one("metodos_pago", "*", codigo=metodo_texto) or read_one("metodos_pago", "*", nombre=metodo_texto.title())
    if not metodo:
        metodo = insert_row(
            "metodos_pago",
            {"codigo": metodo_texto, "nombre": metodo_texto.title(), "activo": True, "es_online": False, "requiere_pasarela": False},
        )
    repuestos = data.get("repuestos_usados") if isinstance(data.get("repuestos_usados"), list) else []
    for item in repuestos:
        pid = entero(item.get("producto_id")) if isinstance(item, dict) else 0
        cantidad = entero(item.get("cantidad")) if isinstance(item, dict) else 0
        if pid > 0 and cantidad > 0:
            mover_stock(pid, "SALIDA", cantidad, f"Reparación REP-{reparacion_id:04d}", usuario_actual().get("id"))
    update_row(
        "reparaciones",
        {
            "diagnostico": diagnostico,
            "solucion": trabajo,
            "costo_total": monto,
            "estado": "ENTREGADO",
            "fecha_fin": now_utc_iso(),
            "fecha_entrega": now_utc_iso(),
            "notas_tecnico": texto(data.get("nota")) or reparacion.get("notas_tecnico"),
            "fecha_actualizacion": now_utc_iso(),
        },
        id=reparacion_id,
    )
    if reparacion.get("reserva_id"):
        update_row(
            "reservas",
            {"estado": "ATENDIDA", "fecha_actualizacion": now_utc_iso()},
            id=reparacion.get("reserva_id"),
        )
    if reparacion.get("electrodomestico_id"):
        update_row("electrodomesticos", {"estado": "ENTREGADO"}, id=reparacion.get("electrodomestico_id"))
    insert_row(
        "pagos",
        {
            "reparacion_id": reparacion_id,
            "metodo_pago_id": metodo.get("id"),
            "usuario_id": usuario_actual().get("id"),
            "canal_pago": "ONLINE",
            "monto": monto,
            "estado": "CONFIRMADO",
            "pasarela": metodo_texto,
            "transaction_id": f"REP-{reparacion_id:04d}",
            "order_id": f"REP-{reparacion_id:04d}",
            "fecha_pago": now_utc_iso(),
        },
    )
    cliente = read_one("clientes", "*", id=(read_one("reservas", "cliente_id", id=reparacion.get("reserva_id")) or {}).get("cliente_id")) if reparacion.get("reserva_id") else None
    if cliente:
        usuario = usuario_por_cliente(cliente.get("id"))
        if usuario:
            notificar(
                usuario_id=usuario.get("id"),
                cliente_id=cliente.get("id"),
                tipo="REPARACION_CERRADA",
                titulo="Reparación finalizada",
                mensaje=f"La reparación REP-{reparacion_id:04d} fue cerrada. Puedes recoger tu equipo.",
                referencia=f"/repairs/{reparacion_id}",
            )
    serie = f"T001-{reparacion_id:08d}"
    return jsonify(
        {
            "reparacion": {"id": reparacion_id, "ticket": f"REP-{reparacion_id:04d}", "estado": "ENTREGADO"},
            "comprobante": {"serie_numero": serie, "pdf_url": f"/api/v1/staff/repairs/{reparacion_id}/receipt.pdf"},
        }
    )


@bp.get("/staff/availability")
@api_route
@api_auth("TECNICO", "SUPERVISOR", "ADMINISTRADOR")
def staff_availability_list():
    desde = _fecha_arg(request.args.get("desde"))
    dias = arg_entero("dias", 3, 1, 31)
    tecnico_id = arg_entero("tecnico_id", 0, 0)
    if not _es_superior():
        tecnico_id = _tecnico_id_actual()
    hasta = desde + timedelta(days=dias - 1)
    filas = read_rows("agenda_tecnicos", "*", order="fecha")
    salida = []
    for f in filas:
        fecha_f = fecha_iso(f.get("fecha"))
        if not fecha_f or not (desde.isoformat() <= fecha_f <= hasta.isoformat()):
            continue
        if tecnico_id and int(f.get("tecnico_id") or 0) != tecnico_id:
            continue
        salida.append(_franja_payload(f))
    salida.sort(key=lambda x: (x["fecha"], x["hora_inicio"]))
    return jsonify({"items": salida, "desde": desde.isoformat(), "hasta": hasta.isoformat()})


@bp.post("/staff/availability")
@api_route
@api_auth("TECNICO")
def staff_availability_create():
    tecnico = _tecnico_actual()
    if not tecnico:
        raise ApiError("Tu usuario no tiene un técnico asociado.", 400, "DATOS_INVALIDOS")
    data = cuerpo()
    fecha = validar_fecha(texto(data.get("fecha")))
    inicio = validar_hora(data.get("hora_inicio"))
    fin = validar_hora(data.get("hora_fin"))
    if fin <= inicio:
        raise ApiError("La hora fin debe ser posterior a la hora inicio.", 400, "DATOS_INVALIDOS")
    repetir = texto(data.get("repetir_hasta"))
    hasta = fecha
    if repetir:
        try:
            hasta = date.fromisoformat(repetir)
        except ValueError as exc:
            raise ApiError("La fecha de repetición no es válida.", 400, "DATOS_INVALIDOS") from exc
        if hasta < fecha:
            raise ApiError("La fecha de repetición no puede ser anterior.", 400, "DATOS_INVALIDOS")
        if (hasta - fecha).days > 90:
            raise ApiError("La repetición no puede superar 90 días.", 400, "DATOS_INVALIDOS")
    existentes = read_rows("agenda_tecnicos", "*", tecnico_id=tecnico.get("id"))
    creadas = []
    dia = fecha
    while dia <= hasta:
        choque = False
        for f in existentes:
            if fecha_iso(f.get("fecha")) != dia.isoformat():
                continue
            hi = str(f.get("hora_inicio") or "")[:5]
            hf = str(f.get("hora_fin") or "")[:5]
            if hi and hf and hi < fin and hf > inicio:
                choque = True
                break
        if not choque:
            creadas.append(
                insert_row(
                    "agenda_tecnicos",
                    {
                        "tecnico_id": tecnico.get("id"),
                        "fecha": dia.isoformat(),
                        "hora_inicio": inicio,
                        "hora_fin": fin,
                        "disponible": True,
                        "estado": "ACTIVA",
                    },
                )
            )
        dia += timedelta(days=1)
    if not creadas:
        raise ApiError("La franja se cruza con otra ya registrada.", 409, "HORARIO_DUPLICADO")
    return jsonify({**_franja_payload(creadas[0]), "ids": [f.get("id") for f in creadas]}), 201


def _franja_propietario(franja_id: int) -> dict:
    fila = read_one("agenda_tecnicos", "*", id=franja_id)
    if not fila:
        raise ApiError("La franja no existe.", 404, "NO_ENCONTRADO")
    if int(fila.get("tecnico_id") or 0) != _tecnico_id_actual():
        raise ApiError("La franja pertenece a otro técnico.", 403, "SIN_PERMISO")
    return fila


@bp.patch("/staff/availability/<int:franja_id>")
@api_route
@api_auth("TECNICO")
def staff_availability_update(franja_id: int):
    fila = _franja_propietario(franja_id)
    data = cuerpo()
    if "activo" not in data:
        raise ApiError("El campo activo es obligatorio.", 400, "DATOS_INVALIDOS")
    activo = booleano(data.get("activo"))
    actualizada = update_row(
        "agenda_tecnicos",
        {"disponible": activo, "estado": "ACTIVA"},
        id=fila.get("id"),
    ) or {**fila, "disponible": activo, "estado": "ACTIVA"}
    return jsonify(_franja_payload(actualizada))


@bp.delete("/staff/availability/<int:franja_id>")
@api_route
@api_auth("TECNICO")
def staff_availability_delete(franja_id: int):
    fila = _franja_propietario(franja_id)
    delete_row("agenda_tecnicos", id=fila.get("id"))
    return "", 204


@bp.post("/staff/availability/block-lunch")
@api_route
@api_auth("TECNICO")
def staff_block_lunch():
    tecnico = _tecnico_actual()
    if not tecnico:
        raise ApiError("Tu usuario no tiene un técnico asociado.", 400, "DATOS_INVALIDOS")
    data = cuerpo()
    fecha = validar_fecha(texto(data.get("fecha")))
    inicio = validar_hora(data.get("hora_inicio") or "13:00")
    fin = validar_hora(data.get("hora_fin") or "14:00")
    if fin <= inicio:
        raise ApiError("La hora fin debe ser posterior a la hora inicio.", 400, "DATOS_INVALIDOS")
    fila = insert_row(
        "agenda_tecnicos",
        {
            "tecnico_id": tecnico.get("id"),
            "fecha": fecha.isoformat(),
            "hora_inicio": inicio,
            "hora_fin": fin,
            "disponible": False,
            "estado": "ACTIVA",
        },
    )
    payload = _franja_payload(fila)
    payload["activo"] = False
    return jsonify(payload), 201


@bp.get("/staff/van-stock")
@api_route
@api_auth("TECNICO", "ALMACENERO", "SUPERVISOR", "ADMINISTRADOR")
def staff_van_stock():
    tecnico_id = arg_entero("tecnico_id", 0, 0)
    if contexto_actual().get("rol") == "TECNICO":
        tecnico_id = _tecnico_id_actual()
    if tecnico_id:
        items = _van_items(tecnico_id)
    else:
        acumulado = {}
        for meta in read_rows("notificaciones", "referencia", tipo="VAN_STOCK"):
            try:
                valor = json.loads(meta.get("mensaje") or "{}")
            except (TypeError, ValueError):
                valor = {}
            for item in valor.get("items") if isinstance(valor, dict) else []:
                if not isinstance(item, dict):
                    continue
                pid = entero(item.get("producto_id"))
                cantidad = entero(item.get("cantidad"))
                if pid > 0 and cantidad > 0:
                    acumulado[pid] = acumulado.get(pid, 0) + cantidad
        items = []
        for pid, cantidad in acumulado.items():
            p = producto_por_id(pid)
            if p:
                items.append(
                    {
                        "producto_id": pid,
                        "sku": p.get("codigo") or f"PRO-{pid}",
                        "nombre": p.get("nombre") or "",
                        "cantidad": cantidad,
                    }
                )
    if not items:
        # Si aún no hay valija registrada, se muestra un resumen útil de repuestos.
        items = []
    return jsonify({"total": sum(i["cantidad"] for i in items), "items": items})
