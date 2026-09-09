"""Agendamiento de citas de servicio técnico (flujo público de cliente).

Crea/actualiza un `cliente`, registra su `electrodomestico` y guarda la
`reserva` (cita) en estado PENDIENTE. Todo se persiste vía Supabase.
"""

import logging
from datetime import date as date_cls

from app.supabase_client import get_admin_client

log = logging.getLogger(__name__)

TIPO_ATENCIONES = {"TALLER", "DOMICILIO"}
HORARIOS = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"]
ESTADO_RESERVA = "PENDIENTE"
ESTADO_ELECTRODOMESTICO = "REGISTRADO"


class CitaError(Exception):
    """Error de validación o de persistencia del agendamiento."""


def _fecha_hoy():
    return date_cls.today()


def _plus_one_hour(hora: str) -> str:
    """Suma una hora a 'HH:MM' y devuelve 'HH:MM'."""
    try:
        h, m = map(int, hora.split(":"))
        return f"{(h + 1) % 24:02d}:{m:02d}"
    except (TypeError, ValueError):
        return ""


def validar(datos: dict) -> None:
    """Valida los datos del formulario y lanza CitaError si algo falla."""
    if not datos.get("numero_documento", "").strip():
        raise CitaError("Indica tu número de documento.")
    if not datos.get("nombres", "").strip() and not datos.get("razon_social", "").strip():
        raise CitaError("Indica tu nombre y apellido (o razón social).")
    if not datos.get("telefono", "").strip():
        raise CitaError("Indica tu teléfono de contacto.")

    tipo_atencion = (datos.get("tipo_atencion") or "").upper()
    if tipo_atencion not in TIPO_ATENCIONES:
        raise CitaError("Selecciona un tipo de atención válido.")
    if tipo_atencion == "DOMICILIO" and not datos.get("calle", "").strip():
        raise CitaError("Para atención a domicilio indica tu dirección completa.")

    fecha = datos.get("fecha_reserva", "").strip()
    if not fecha:
        raise CitaError("Selecciona la fecha de tu cita.")
    try:
        fecha_ok = date_cls.fromisoformat(fecha)
    except ValueError:
        raise CitaError("La fecha de la cita no es válida.") from None
    if fecha_ok < _fecha_hoy():
        raise CitaError("La fecha de la cita no puede estar en el pasado.")

    hora = datos.get("hora_inicio", "").strip()
    if hora not in HORARIOS:
        raise CitaError("Selecciona un horario disponible.")


def _normalizar(datos: dict) -> dict:
    def _v(campo):
        return (datos.get(campo) or "").strip()

    tipo_atencion = _v("tipo_atencion").upper()
    return {
        "tipo_documento_id": int(datos.get("tipo_documento_id") or 1),
        "numero_documento": _v("numero_documento"),
        "nombres": _v("nombres"),
        "apellidos": _v("apellidos"),
        "razon_social": _v("razon_social"),
        "telefono": _v("telefono"),
        "correo": _v("correo"),
        "tipo_id": int(datos.get("tipo_id") or 0),
        "marca_id": int(datos.get("marca_id") or 0),
        "modelo": _v("modelo"),
        "numero_serie": _v("numero_serie"),
        "color": _v("color"),
        "tipo_atencion": tipo_atencion,
        "fecha_reserva": _v("fecha_reserva"),
        "hora_inicio": _v("hora_inicio"),
        "motivo": _v("motivo"),
        "observaciones": _v("observaciones"),
        "calle": _v("calle"),
    }


def _find_cliente(db, tipo_documento_id: int, numero_documento: str):
    try:
        resp = (
            db.from_("clientes")
            .select("id,nombres,apellidos,telefono,correo")
            .eq("tipo_documento_id", tipo_documento_id)
            .eq("numero_documento", numero_documento)
            .limit(1)
            .execute()
        )
        return (resp.data or [None])[0]
    except Exception:  # noqa: BLE001
        return None


def _crear_cliente(db, d: dict):
    fila = {
        "tipo_documento_id": d["tipo_documento_id"],
        "numero_documento": d["numero_documento"],
        "nombres": d["nombres"],
        "apellidos": d["apellidos"],
        "razon_social": d["razon_social"] or None,
        "telefono": d["telefono"],
        "correo": d["correo"] or None,
        "activo": True,
    }
    resp = db.from_("clientes").insert(fila).execute()
    return (resp.data or [{}])[0].get("id")


def _crear_electrodomestico(db, cliente_id: int, d: dict):
    fila = {
        "cliente_id": cliente_id,
        "tipo_id": d["tipo_id"],
        "marca_id": d["marca_id"],
        "modelo": d["modelo"] or None,
        "numero_serie": d["numero_serie"] or None,
        "color": d["color"] or None,
        "observaciones": d["observaciones"] or None,
        "estado": ESTADO_ELECTRODOMESTICO,
    }
    resp = db.from_("electrodomesticos").insert(fila).execute()
    return (resp.data or [{}])[0].get("id")


def _crear_reserva(db, cliente_id: int, electrodomestico_id: int, d: dict):
    hora_fin = _plus_one_hour(d["hora_inicio"])
    fila = {
        "cliente_id": cliente_id,
        "electrodomestico_id": electrodomestico_id,
        "tipo_atencion": d["tipo_atencion"],
        "fecha_reserva": d["fecha_reserva"],
        "hora_inicio": d["hora_inicio"],
        "hora_fin": hora_fin,
        "estado": ESTADO_RESERVA,
        "motivo": d["motivo"] or None,
        "observaciones": d["observaciones"] or None,
        "calle": d["calle"] or None,
    }
    resp = db.from_("reservas").insert(fila).execute()
    return (resp.data or [{}])[0].get("id")


def agendar(datos: dict) -> dict:
    """Crea la cita completa. Devuelve el id de la reserva.

    Lanza CitaError si la validación o la escritura fallan.
    """
    validar(datos)
    d = _normalizar(datos)
    db = get_admin_client()

    try:
        cliente = _find_cliente(db, d["tipo_documento_id"], d["numero_documento"])
        if cliente:
            cliente_id = cliente["id"]
            actualizar = {}
            if d.get("telefono") and not cliente.get("telefono"):
                actualizar["telefono"] = d["telefono"]
            if d.get("correo") and not cliente.get("correo"):
                actualizar["correo"] = d["correo"]
            if actualizar:
                db.from_("clientes").update(actualizar).eq("id", cliente_id).execute()
        else:
            cliente_id = _crear_cliente(db, d)

        electrodomestico_id = _crear_electrodomestico(db, cliente_id, d)
        reserva_id = _crear_reserva(db, cliente_id, electrodomestico_id, d)

        if not reserva_id:
            raise CitaError("No se pudo registrar la cita. Inténtalo nuevamente.")
        return {"reserva_id": reserva_id, "cliente_id": cliente_id}
    except CitaError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("Error agendando cita: %s", exc)
        raise CitaError(
            "No se pudo guardar la cita en este momento. Verifica la conexión y vuelve a intentar."
        ) from exc