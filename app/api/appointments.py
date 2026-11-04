"""Endpoints de citas E22–E27."""

from __future__ import annotations

import json
import uuid

from flask import Response, jsonify, request

from app.api import bp
from app.api.common import (
    ApiError,
    ahora_lima,
    api_auth,
    api_route,
    appointments_payload,
    cita_por_id,
    contexto_actual,
    cuerpo,
    decimal,
    entero,
    hoy_lima,
    insert_row,
    meta_guardar,
    meta_obtener,
    now_utc_iso,
    puede_ver_cita,
    read_one,
    read_rows,
    requerir,
    sumar_hora,
    texto,
    validar_fecha,
    validar_hora,
)
from app.services import storage_service

HORARIOS = [f"{h:02d}:00" for h in range(9, 17)]
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


def _horas_franja() -> list[tuple[str, str]]:
    return [(h, sumar_hora(h, 1)) for h in HORARIOS]


def _tecnicos_disponibles(fecha: str, inicio: str, fin: str) -> int:
    """Cuenta técnicos libres en la franja, restando citas ya asignadas."""
    agenda = read_rows("agenda_tecnicos", "*", fecha=fecha)
    tecnicos = read_rows("tecnicos", "id", order="id")
    total_tecnicos = len(tecnicos)
    if total_tecnicos == 0:
        total_tecnicos = 1  # agenda operativa aunque aún no haya técnicos cargados

    if agenda:
        ids = set()
        for fila in agenda:
            if fila.get("disponible") is False:
                continue
            if (fila.get("estado") or "").upper() in {"INACTIVO", "BLOQUEADO", "CANCELADO"}:
                continue
            hi = str(fila.get("hora_inicio") or "")[:5]
            hf = str(fila.get("hora_fin") or "")[:5]
            if hi and hf and hi <= inicio and hf >= fin:
                ids.add(fila.get("tecnico_id"))
        total = len(ids)
    else:
        total = int(total_tecnicos)

    reservas = read_rows("reservas", "id,tecnico_id,hora_inicio,hora_fin,estado", fecha_reserva=fecha)
    ocupados = set()
    for r in reservas:
        if (r.get("estado") or "").upper() == "CANCELADA":
            continue
        if not r.get("tecnico_id"):
            continue
        hi = str(r.get("hora_inicio") or "")[:5]
        hf = str(r.get("hora_fin") or "")[:5]
        if hi and hf and hi < fin and hf > inicio:
            ocupados.add(r.get("tecnico_id"))
    return max(0, int(total) - len(ocupados))


def _horas_disponibles(fecha: str) -> list[dict]:
    ahora = ahora_lima()
    es_hoy = fecha == hoy_lima().isoformat()
    hora_actual = ahora.strftime("%H:%M")
    salida = []
    for inicio, fin in _horas_franja():
        if es_hoy and inicio <= hora_actual:
            continue
        libres = _tecnicos_disponibles(fecha, inicio, fin)
        if libres > 0:
            salida.append({"hora_inicio": inicio, "hora_fin": fin, "tecnicos_disponibles": libres})
    return salida


def _cita_limpia(reserva: dict) -> dict:
    datos = appointments_payload([reserva])[0]
    datos.pop("_meta", None)
    return datos


def _foto_urls(foto_ids: list) -> list[str]:
    urls = []
    for foto_id in foto_ids:
        fila = read_one("notificaciones", "titulo", id=entero(foto_id), tipo="CITA_FOTO")
        if fila and fila.get("titulo"):
            urls.append(fila.get("titulo"))
    return [u for u in dict.fromkeys(urls) if u]


@bp.get("/appointments/catalogs")
@api_route
@api_auth("CLIENTE", "TECNICO", "RECEPCIONISTA", "SUPERVISOR", "ADMINISTRADOR")
def appointments_catalogs():
    tipos = read_rows("tipos_electrodomestico", "id,nombre", order="nombre")
    marcas = read_rows("marcas", "id,nombre", order="nombre")
    return jsonify(
        {
            "tipos_equipo": [{"id": t.get("id"), "nombre": t.get("nombre")} for t in tipos],
            "marcas": [{"id": m.get("id"), "nombre": m.get("nombre")} for m in marcas],
            "tipos_atencion": ["TALLER", "DOMICILIO"],
            "horario": {"inicio": "09:00", "fin": "17:00"},
        }
    )


@bp.get("/appointments/availability")
@api_route
@api_auth("CLIENTE", "TECNICO", "RECEPCIONISTA", "SUPERVISOR", "ADMINISTRADOR")
def appointments_availability():
    fecha = validar_fecha(texto(request.args.get("fecha")))
    tipo = texto(request.args.get("tipo_atencion")).upper()
    if tipo not in {"TALLER", "DOMICILIO"}:
        raise ApiError("El tipo de atención debe ser TALLER o DOMICILIO.", 400, "DATOS_INVALIDOS")
    horas = _horas_disponibles(fecha.isoformat())
    return jsonify({"fecha": fecha.isoformat(), "horas": horas})


@bp.post("/appointments/photos")
@api_route
@api_auth("CLIENTE", "RECEPCIONISTA", "ADMINISTRADOR")
def appointments_photo():
    archivo = request.files.get("archivo") or request.files.get("foto")
    if not archivo or not archivo.filename:
        raise ApiError("Adjunta una imagen en el campo archivo.", 400, "DATOS_INVALIDOS", {"archivo": "obligatorio"})
    nombre = archivo.filename.lower()
    if not nombre.endswith((".jpg", ".jpeg", ".png", ".webp")):
        raise ApiError("Solo se aceptan imágenes JPG, PNG o WEBP.", 400, "DATOS_INVALIDOS", {"archivo": "formato"})
    archivo.stream.seek(0, 2)
    tamano = archivo.stream.tell()
    archivo.stream.seek(0)
    if tamano > 2 * 1024 * 1024:
        raise ApiError("La imagen supera el máximo de 2 MB.", 400, "ARCHIVO_MUY_GRANDE")
    try:
        url, ruta = storage_service.subir_imagen(
            storage_service.BUCKET_EVIDENCIAS,
            f"citas/{uuid.uuid4().hex}",
            archivo,
        )
    except Exception as exc:  # noqa: BLE001
        mensaje = getattr(exc, "message", None) or str(exc) or "No se pudo subir la imagen."
        raise ApiError(mensaje, 400, "ARCHIVO_INVALIDO") from exc
    fila = insert_row(
        "notificaciones",
        {
            "usuario_id": contexto_actual().get("id"),
            "tipo": "CITA_FOTO",
            "titulo": url,
            "mensaje": json.dumps({"ruta": ruta}, ensure_ascii=False),
            "referencia": ruta,
            "canal": "API",
            "leida": False,
            "fecha_creacion": now_utc_iso(),
        },
    )
    return jsonify({"foto_id": fila.get("id"), "url": url}), 201


@bp.post("/appointments")
@api_route
@api_auth("CLIENTE", "RECEPCIONISTA", "ADMINISTRADOR")
def appointments_create():
    data = cuerpo()
    ctx = contexto_actual()
    rol = ctx.get("rol")
    equipo = data.get("equipo") if isinstance(data.get("equipo"), dict) else {}

    if rol == "CLIENTE":
        cliente_id = ctx.get("cliente_id")
        if not cliente_id:
            raise ApiError("Tu cuenta no tiene un cliente asociado.", 400, "DATOS_INVALIDOS")
    else:
        cliente_id = entero(data.get("cliente_id"))
        if cliente_id <= 0:
            raise ApiError("Indica el cliente de la cita.", 400, "DATOS_INVALIDOS", {"cliente_id": "obligatorio"})
        if not read_one("clientes", "id", id=cliente_id):
            raise ApiError("El cliente no existe.", 404, "NO_ENCONTRADO")

    tipo_id = entero(equipo.get("tipo_id"))
    marca_id = entero(equipo.get("marca_id"))
    modelo = texto(equipo.get("modelo"))
    if tipo_id <= 0:
        raise ApiError("El tipo de equipo es obligatorio.", 400, "DATOS_INVALIDOS", {"tipo_id": "obligatorio"})
    if marca_id <= 0:
        raise ApiError("La marca es obligatoria.", 400, "DATOS_INVALIDOS", {"marca_id": "obligatorio"})
    if not modelo:
        raise ApiError("El modelo es obligatorio.", 400, "DATOS_INVALIDOS", {"modelo": "obligatorio"})
    if not read_one("tipos_electrodomestico", "id", id=tipo_id):
        raise ApiError("El tipo de equipo no existe.", 400, "DATOS_INVALIDOS")
    if not read_one("marcas", "id", id=marca_id):
        raise ApiError("La marca no existe.", 400, "DATOS_INVALIDOS")

    fecha = validar_fecha(texto(data.get("fecha")))
    hora_inicio = validar_hora(data.get("hora_inicio"))
    tipo_atencion = texto(data.get("tipo_atencion")).upper()
    if tipo_atencion not in {"TALLER", "DOMICILIO"}:
        raise ApiError("El tipo de atención debe ser TALLER o DOMICILIO.", 400, "DATOS_INVALIDOS")
    direccion = texto(data.get("direccion"))
    if tipo_atencion == "DOMICILIO" and not direccion:
        raise ApiError("La dirección es obligatoria para atención a domicilio.", 400, "DATOS_INVALIDOS", {"direccion": "obligatorio"})
    descripcion = texto(data.get("descripcion_problema"))
    if not 10 <= len(descripcion) <= 500:
        raise ApiError("La descripción del problema debe tener entre 10 y 500 caracteres.", 400, "DATOS_INVALIDOS", {"descripcion_problema": "longitud"})
    hora_fin = sumar_hora(hora_inicio, 1)
    horas = {h["hora_inicio"]: h for h in _horas_disponibles(fecha.isoformat())}
    if hora_inicio not in horas:
        raise ApiError("La hora seleccionada ya no está disponible.", 409, "HORARIO_NO_DISPONIBLE")
    if horas[hora_inicio]["tecnicos_disponibles"] <= 0:
        raise ApiError("No hay técnicos disponibles en esa franja.", 409, "SIN_TECNICOS")

    electro = insert_row(
        "electrodomesticos",
        {
            "cliente_id": cliente_id,
            "tipo_id": tipo_id,
            "marca_id": marca_id,
            "modelo": modelo,
            "numero_serie": texto(equipo.get("serie")) or None,
            "color": texto(equipo.get("color")) or None,
            "observaciones": None,
            "estado": "REGISTRADO",
            "fecha_registro": now_utc_iso(),
        },
    )
    reserva = insert_row(
        "reservas",
        {
            "cliente_id": cliente_id,
            "electrodomestico_id": electro.get("id"),
            "fecha_reserva": fecha.isoformat(),
            "hora_inicio": hora_inicio,
            "hora_fin": hora_fin,
            "tipo_atencion": tipo_atencion,
            "estado": "PENDIENTE",
            "motivo": descripcion,
            "observaciones": texto(data.get("observaciones")) or None,
            "calle": direccion or None,
            "distrito": None,
            "fecha_creacion": now_utc_iso(),
            "fecha_actualizacion": now_utc_iso(),
        },
    )
    foto_ids = data.get("foto_ids") if isinstance(data.get("foto_ids"), list) else []
    meta_guardar(
        "CITA_META",
        reserva.get("id"),
        {
            "direccion": direccion,
            "referencia": texto(data.get("referencia")),
            "latitud": decimal(data.get("latitud"), None) if data.get("latitud") not in (None, "") else None,
            "longitud": decimal(data.get("longitud"), None) if data.get("longitud") not in (None, "") else None,
            "foto_ids": [entero(x) for x in foto_ids if entero(x) > 0],
        },
        usuario_id=ctx.get("id"),
    )
    return jsonify(_cita_limpia(reserva)), 201


@bp.get("/appointments/<int:reserva_id>")
@api_route
@api_auth("CLIENTE", "TECNICO", "RECEPCIONISTA", "SUPERVISOR", "ADMINISTRADOR")
def appointments_get(reserva_id: int):
    reserva = cita_por_id(reserva_id)
    if not reserva:
        raise ApiError("La cita no existe.", 404, "NO_ENCONTRADO")
    if not puede_ver_cita(reserva):
        raise ApiError("No tienes permiso para ver esta cita.", 403, "SIN_PERMISO")
    return jsonify(_cita_limpia(reserva))


@bp.get("/appointments/<int:reserva_id>/ticket.pdf")
@api_route
@api_auth("CLIENTE", "TECNICO", "RECEPCIONISTA", "SUPERVISOR", "ADMINISTRADOR")
def appointments_ticket(reserva_id: int):
    from app.services import pdf_service

    reserva = cita_por_id(reserva_id)
    if not reserva:
        raise ApiError("La cita no existe.", 404, "NO_ENCONTRADO")
    if not puede_ver_cita(reserva):
        raise ApiError("No tienes permiso para ver esta cita.", 403, "SIN_PERMISO")
    datos = _cita_limpia(reserva)
    orden = {
        "codigo": datos.get("codigo"),
        "fecha_ingreso": f"{datos.get('fecha')} {datos.get('hora_inicio')}",
        "estado": datos.get("estado"),
        "tipo_atencion": datos.get("tipo_atencion"),
        "cliente_nombre": (datos.get("cliente") or {}).get("nombre"),
        "cliente_telefono": (datos.get("cliente") or {}).get("telefono"),
        "direccion": datos.get("direccion"),
        "equipo": " ".join(
            x
            for x in (
                (datos.get("equipo") or {}).get("tipo"),
                (datos.get("equipo") or {}).get("marca"),
                (datos.get("equipo") or {}).get("modelo"),
            )
            if x
        ),
        "numero_serie": (datos.get("equipo") or {}).get("serie"),
        "problema": datos.get("descripcion_problema"),
        "notas": datos.get("observaciones"),
        "tecnico_nombre": (datos.get("tecnico") or {}).get("nombre"),
    }
    pdf = pdf_service.generar_orden_servicio_pdf(orden)
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={datos.get('codigo')}.pdf"},
    )
