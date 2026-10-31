"""Agendamiento de citas de servicio técnico (flujo público de cliente).

Crea/actualiza un `cliente`, registra su `electrodomestico` y guarda la
`reserva` (cita) en estado PENDIENTE. Todo se persiste vía Supabase.
"""

import logging
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

from app.config import Config
from app.supabase_client import get_admin_client, get_reader

log = logging.getLogger(__name__)

TIPO_ATENCIONES = {"TALLER", "DOMICILIO"}
HORARIOS = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"]
ESTADO_RESERVA = "PENDIENTE"
ESTADO_ELECTRODOMESTICO = "REGISTRADO"

# Distritos donde hay cobertura a domicilio (se configura en app/config.py).
DISTRITOS_DOMICILIO = list(Config.DISTRITOS_DOMICILIO)
# La columna `reservas.calle` guarda "Distrito - Dirección". Se separa con " - "
# para que el formulario pueda volver a mostrar el distrito elegido si la
# validación falla.
SEPARADOR_DIRECCION = " - "


class CitaError(Exception):
    """Error de validación o de persistencia del agendamiento."""


def _fecha_hoy():
    return date_cls.today()


def _ahora() -> str:
    """Marca de tiempo en UTC, igual que usa el carrito."""
    return datetime.now(timezone.utc).isoformat()


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
    distrito = (datos.get("distrito") or "").strip()
    if tipo_atencion == "DOMICILIO":
        if not distrito:
            raise CitaError("Selecciona el distrito de la atención a domicilio.")
        if distrito not in DISTRITOS_DOMICILIO:
            raise CitaError("Solo atendemos a domicilio en nuestros distritos de cobertura.")

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

    if hora not in horarios_disponibles(fecha):
        raise CitaError(
            "En esa fecha y hora no hay un técnico disponible. "
            "Elige otra hora o fecha."
        )

    tipo_id = datos.get("tipo_id") or datos.get("tipo_id_alt") or ""
    try:
        tipo_id_val = int(tipo_id) if str(tipo_id).strip() else 0
    except (TypeError, ValueError):
        tipo_id_val = 0
    tipo_otro = (datos.get("tipo_otro") or "").strip()
    if not tipo_id_val and not tipo_otro:
        raise CitaError("Selecciona el tipo de equipo.")
    if tipo_otro and len(tipo_otro) > 30:
        raise CitaError("El tipo de equipo personalizado debe tener máximo 30 caracteres.")

    marca_id = datos.get("marca_id") or datos.get("marca_id_alt") or ""
    try:
        marca_id_val = int(marca_id) if str(marca_id).strip() else 0
    except (TypeError, ValueError):
        marca_id_val = 0
    marca_otro = (datos.get("marca_otro") or "").strip()
    if not marca_id_val and not marca_otro:
        raise CitaError("Selecciona la marca del equipo.")
    if marca_otro and len(marca_otro) > 30:
        raise CitaError("La marca personalizada debe tener máximo 30 caracteres.")

    if len((datos.get("motivo") or "").strip()) > 100:
        raise CitaError("La descripción del problema debe tener máximo 100 caracteres.")
    if len((datos.get("observaciones") or "").strip()) > 100:
        raise CitaError("Las observaciones deben tener máximo 100 caracteres.")


def horarios_disponibles(fecha: str) -> list:
    """Horas de HORARIOS con técnico disponible en la fecha indicada.

    Un técnico cubre la franja [hora_inicio, hora_fin) de `agenda_tecnicos`.
    Si la fecha no tiene ninguna franja cargada (lun-sáb) se asume el horario
    base 09:00-18:00. Para hoy solo se ofrecen las horas que aún no pasaron.
    """
    lima = datetime.now(timezone(timedelta(hours=-5)))
    try:
        dia = date_cls.fromisoformat(fecha)
    except (TypeError, ValueError):
        return []
    if dia < lima.date():
        return []
    try:
        franjas = (
            get_reader()
            .table("agenda_tecnicos")
            .select("hora_inicio,hora_fin,disponible")
            .eq("fecha", fecha)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001
        return []
    if franjas:
        franjas = [f for f in franjas if f.get("disponible")]
    elif dia.weekday() != 6:
        franjas = [{"hora_inicio": HORARIOS[0], "hora_fin": "18:00"}]
    disponibles = [h for h in HORARIOS if any(_cubre_franja(f, h) for f in franjas)]
    if dia == lima.date():
        disponibles = [h for h in disponibles if h > lima.strftime("%H:%M")]
    return disponibles


def _cubre_franja(franja: dict, hora: str) -> bool:
    inicio = (franja.get("hora_inicio") or "")[:5]
    fin = (franja.get("hora_fin") or "")[:5]
    if not inicio:
        return False
    if not fin or fin <= inicio:
        return True
    return inicio <= hora < fin


def datos_usuario(usuario_id) -> dict:
    """Datos del usuario logueado para autollenar 'Tus datos'.

    Un cliente registrado se vincula al usuario vía `clientes` (cliente_id);
    los roles internos vía `empleados` (empleado_id).
    """
    if not usuario_id:
        return {}
    try:
        u = (
            get_reader()
            .table("usuarios")
            .select("empleado_id,cliente_id")
            .eq("id", usuario_id)
            .limit(1)
            .execute()
            .data
            or [{}]
        )[0]
    except Exception:  # noqa: BLE001
        return {}

    relacion, campo_id = ("clientes", "cliente_id") if u.get("cliente_id") else ("empleados", "empleado_id")
    persona_id = u.get(campo_id)
    if not persona_id:
        return {}
    columnas = "tipo_documento_id,numero_documento,nombres,apellidos,telefono,correo"
    try:
        persona = (
            get_reader()
            .table(relacion)
            .select(columnas)
            .eq("id", persona_id)
            .limit(1)
            .execute()
            .data
            or [None]
        )[0]
    except Exception:  # noqa: BLE001
        return {}
    if not persona:
        return {}
    return {
        "tipo_documento_id": persona.get("tipo_documento_id") or 1,
        "numero_documento": persona.get("numero_documento") or "",
        "nombres": persona.get("nombres") or "",
        "apellidos": persona.get("apellidos") or "",
        "telefono": persona.get("telefono") or "",
        "correo": persona.get("correo") or "",
    }


# --- Seguimiento de citas para el cliente ---------------------------------

ESTADO_CITA_UI = {
    "PENDIENTE": {
        "label": "Pendiente de confirmación",
        "badge": "text-bg-warning",
        "icono": "hourglass-split",
        "mensaje": "Recibimos tu solicitud. Te contactaremos para confirmar la cita.",
    },
    "CONFIRMADA": {
        "label": "Confirmada",
        "badge": "text-bg-info",
        "icono": "calendar-check",
        "mensaje": "Tu cita está confirmada. Un técnico fue asignado para atenderte.",
    },
    "EN_ATENCION": {
        "label": "En atención",
        "badge": "text-bg-primary",
        "icono": "tools",
        "mensaje": "El técnico ya está trabajando en la atención de tu cita.",
    },
    "COMPLETADA": {
        "label": "Completada",
        "badge": "text-bg-success",
        "icono": "check-circle",
        "mensaje": "La atención de tu cita finalizó correctamente.",
    },
    "ATENDIDA": {
        "label": "Atendida",
        "badge": "text-bg-success",
        "icono": "check-circle",
        "mensaje": "La atención de tu cita finalizó correctamente.",
    },
    "CANCELADA": {
        "label": "Cancelada",
        "badge": "text-bg-danger",
        "icono": "x-circle",
        "mensaje": "Esta cita fue cancelada. Puedes agendar una nueva cuando lo necesites.",
    },
}

ESTADOS_CITA_ACTIVOS = ("PENDIENTE", "CONFIRMADA", "EN_ATENCION")
ESTADOS_CITA_FINALES = ("COMPLETADA", "ATENDIDA")

_ORDEN_ESTADO_CITA = {
    "PENDIENTE": 0,
    "CONFIRMADA": 1,
    "EN_ATENCION": 2,
    "COMPLETADA": 3,
    "ATENDIDA": 3,
}

_ALIAS_ESTADO_CITA = {
    "ATENDIDO": "ATENDIDA",
    "EN_PROCESO": "EN_ATENCION",
    "FINALIZADA": "COMPLETADA",
}

ESTADO_REPARACION_UI = {
    "RECIBIDO": ("Recibido", "text-bg-secondary", "inbox"),
    "DIAGNOSTICO": ("En diagnóstico", "text-bg-info", "search"),
    "ESPERANDO_APROBACION": ("Esperando aprobación", "text-bg-warning", "hourglass-split"),
    "APROBADO": ("Aprobado", "text-bg-info", "check2-circle"),
    "ESPERANDO_REPUESTO": ("Esperando repuesto", "text-bg-warning", "box-seam"),
    "EN_REPARACION": ("En reparación", "text-bg-primary", "tools"),
    "REPARADO": ("Reparado", "text-bg-success", "check-circle"),
    "LISTO_ENTREGA": ("Listo para entrega", "text-bg-success", "box2-heart"),
    "ENTREGADO": ("Entregado", "text-bg-success", "bag-check"),
    "NO_REPARABLE": ("No reparable", "text-bg-danger", "x-octagon"),
    "CANCELADO": ("Cancelado", "text-bg-danger", "x-circle"),
}

# Columnas para el seguimiento. `distrito` no se incluye porque es opcional en
# la base (migración 04) y la dirección completa ya vive en `calle`.
_CAMPOS_CITA_CLIENTE = (
    "id,fecha_reserva,hora_inicio,hora_fin,estado,tipo_atencion,motivo,"
    "observaciones,calle,fecha_creacion,fecha_actualizacion,"
    "fecha_primera_atencion,"
    "electrodomesticos(modelo,numero_serie,marcas(nombre),"
    "tipos_electrodomestico(nombre)),"
    "tecnicos(id,empleados(nombres,apellidos))"
)

_CAMPOS_CITA_CLIENTE_BASE = (
    "id,fecha_reserva,hora_inicio,hora_fin,estado,tipo_atencion,motivo,"
    "observaciones,calle,fecha_creacion,fecha_actualizacion,"
    "fecha_primera_atencion,electrodomestico_id,tecnico_id"
)

_CAMPOS_REPARACION_CLIENTE = (
    "id,reserva_id,estado,problema,fecha_ingreso,fecha_actualizacion,"
    "fecha_entrega,garantia_dias,costo_total"
)


def _normalizar_estado_cita(estado) -> str:
    """Normaliza el estado guardado en BD (incluye valores históricos)."""
    valor = (str(estado or "PENDIENTE")).strip().upper()
    return _ALIAS_ESTADO_CITA.get(valor, valor)


def _fecha_corta(valor) -> str:
    """Devuelve `DD/MM/AAAA` a partir de una fecha ISO."""
    texto = str(valor or "").strip()
    if not texto:
        return ""
    try:
        return date_cls.fromisoformat(texto[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return texto[:10]


def _fecha_hora_corta(valor) -> str:
    """Devuelve `DD/MM/AAAA HH:MM` a partir de una marca ISO."""
    texto = str(valor or "").strip()
    if not texto:
        return ""
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return texto[:16].replace("T", " ")


def _hora_corta(valor) -> str:
    return str(valor or "").strip()[:5]


def _nombre_tecnico(reserva: dict) -> str:
    tecnico = reserva.get("tecnicos") or {}
    empleado = tecnico.get("empleados") or {}
    return " ".join(
        parte for parte in (empleado.get("nombres"), empleado.get("apellidos")) if parte
    ).strip()


def _equipo_cita(reserva: dict) -> tuple:
    """Devuelve (texto del equipo, número de serie) a partir del join."""
    equipo = reserva.get("electrodomesticos") or {}
    tipo = (equipo.get("tipos_electrodomestico") or {}).get("nombre")
    marca = (equipo.get("marcas") or {}).get("nombre")
    modelo = equipo.get("modelo")
    partes = [str(p).strip() for p in (tipo, marca, modelo) if p and str(p).strip()]
    return " · ".join(partes), (equipo.get("numero_serie") or "").strip()


def _pasos_cita(reserva: dict, estado: str, reparacion: dict | None) -> list:
    """Arma la línea de tiempo visible para el cliente."""
    reparacion = reparacion or {}
    indice = _ORDEN_ESTADO_CITA.get(estado, 0)
    cancelada = estado == "CANCELADA"
    definiciones = [
        (
            "Solicitud recibida",
            "Registramos tu cita en el sistema.",
            _fecha_corta(reserva.get("fecha_creacion")),
        ),
        (
            "Cita confirmada",
            "Asignamos un técnico y confirmamos tu cita.",
            _fecha_corta(reserva.get("fecha_primera_atencion")),
        ),
        (
            "En atención",
            "El técnico está revisando tu equipo.",
            _fecha_corta(reparacion.get("fecha_ingreso")),
        ),
        (
            "Atención completada",
            "La cita terminó correctamente.",
            _fecha_corta(
                reparacion.get("fecha_entrega") or reserva.get("fecha_actualizacion")
            ),
        ),
    ]

    pasos = []
    for posicion, (titulo, descripcion, fecha) in enumerate(definiciones):
        if cancelada:
            estado_paso = "hecho" if posicion == 0 else "pendiente"
        elif posicion < indice or (
            posicion == indice and estado in ESTADOS_CITA_FINALES
        ):
            estado_paso = "hecho"
        elif posicion == indice:
            estado_paso = "activo"
        else:
            estado_paso = "pendiente"
        pasos.append({
            "titulo": titulo,
            "descripcion": descripcion,
            "fecha": fecha if estado_paso != "pendiente" else "",
            "estado": estado_paso,
        })
    return pasos


def _reparacion_cita(reparacion: dict | None) -> dict | None:
    if not reparacion:
        return None
    estado = (str(reparacion.get("estado") or "")).strip().upper()
    label, badge, icono = ESTADO_REPARACION_UI.get(
        estado, (estado.replace("_", " ").title() or "En taller", "text-bg-secondary", "wrench")
    )
    return {
        "id": reparacion.get("id"),
        "estado": estado,
        "label": label,
        "badge": badge,
        "icono": icono,
        "fecha": _fecha_corta(
            reparacion.get("fecha_actualizacion") or reparacion.get("fecha_ingreso")
        ),
    }


def _leer_citas_cliente(cliente_id) -> list:
    """Citas del cliente. Si el join falla, se reintenta sin relaciones."""
    for campos in (_CAMPOS_CITA_CLIENTE, _CAMPOS_CITA_CLIENTE_BASE):
        try:
            return (
                get_reader()
                .table("reservas")
                .select(campos)
                .eq("cliente_id", cliente_id)
                .order("fecha_reserva", desc=True)
                .order("hora_inicio", desc=True)
                .limit(200)
                .execute()
                .data
                or []
            )
        except Exception:  # noqa: BLE001
            continue
    log.exception("No se pudieron cargar las citas del cliente %s", cliente_id)
    return []


def _leer_reparaciones_citas(reserva_ids: list) -> dict:
    """Última orden de reparación por cita (si el equipo pasó al taller)."""
    if not reserva_ids:
        return {}
    try:
        filas = (
            get_reader()
            .table("reparaciones")
            .select(_CAMPOS_REPARACION_CLIENTE)
            .in_("reserva_id", reserva_ids)
            .order("id", desc=True)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001
        log.exception("No se pudieron cargar las reparaciones de las citas")
        return {}

    por_reserva = {}
    for fila in filas:
        reserva_id = fila.get("reserva_id")
        if reserva_id is not None:
            por_reserva.setdefault(reserva_id, fila)
    return por_reserva


def listar_citas_cliente(cliente_id) -> dict:
    """Citas y seguimiento del cliente, agrupados para la vista pública.

    Devuelve ``activas`` (pendiente/confirmada/en atención), ``historial``
    (completadas, atendidas o canceladas) y un ``resumen`` con los contadores.
    """
    vacio = {
        "citas": [],
        "activas": [],
        "historial": [],
        "resumen": {"total": 0, "activas": 0, "completadas": 0, "canceladas": 0},
    }
    if not cliente_id:
        return vacio

    filas = _leer_citas_cliente(cliente_id)
    reparaciones = _leer_reparaciones_citas([
        fila.get("id") for fila in filas if fila.get("id") is not None
    ])

    citas = []
    for fila in filas:
        estado = _normalizar_estado_cita(fila.get("estado"))
        info = ESTADO_CITA_UI.get(
            estado,
            {
                "label": estado.replace("_", " ").title() or "Pendiente",
                "badge": "text-bg-secondary",
                "icono": "dot",
                "mensaje": "Consulta con nosotros el estado de tu cita.",
            },
        )
        equipo, numero_serie = _equipo_cita(fila)
        reparacion = reparaciones.get(fila.get("id"))
        tipo_atencion = (fila.get("tipo_atencion") or "").strip().upper()

        citas.append({
            "id": fila.get("id"),
            "fecha": (fila.get("fecha_reserva") or "")[:10],
            "fecha_legible": _fecha_corta(fila.get("fecha_reserva")),
            "hora": _hora_corta(fila.get("hora_inicio")),
            "hora_fin": _hora_corta(fila.get("hora_fin")),
            "estado_raw": estado,
            "estado_label": info["label"],
            "badge": info["badge"],
            "icono": info["icono"],
            "mensaje": info["mensaje"],
            "tipo_atencion": tipo_atencion,
            "tipo_atencion_label": (
                "A domicilio" if tipo_atencion == "DOMICILIO" else "En taller"
            ),
            "equipo": equipo or "Equipo registrado",
            "numero_serie": numero_serie,
            "motivo": (fila.get("motivo") or "").strip(),
            "observaciones": (fila.get("observaciones") or "").strip(),
            "direccion": (fila.get("calle") or "").strip(),
            "tecnico": _nombre_tecnico(fila),
            "creada": _fecha_hora_corta(fila.get("fecha_creacion")),
            "actualizada": _fecha_hora_corta(fila.get("fecha_actualizacion")),
            "pasos": _pasos_cita(fila, estado, reparacion),
            "reparacion": _reparacion_cita(reparacion),
        })

    activas = [c for c in citas if c["estado_raw"] in ESTADOS_CITA_ACTIVOS]
    historial = [c for c in citas if c["estado_raw"] not in ESTADOS_CITA_ACTIVOS]
    activas.sort(key=lambda c: (c["fecha"], c["hora"]))
    historial.sort(key=lambda c: (c["fecha"], c["hora"]), reverse=True)

    return {
        "citas": activas + historial,
        "activas": activas,
        "historial": historial,
        "resumen": {
            "total": len(citas),
            "activas": len(activas),
            "completadas": sum(
                1 for c in citas if c["estado_raw"] in ESTADOS_CITA_FINALES
            ),
            "canceladas": sum(1 for c in citas if c["estado_raw"] == "CANCELADA"),
        },
    }


def seed_demo() -> dict:
    """Crea técnicos desde los usuarios con rol TECNICO y su agenda demo.

    Solo inserta lo que falta: no duplica técnicos existentes (mismo
    empleado_id) ni franjas ya registradas para (tecnico_id, fecha).
    """
    db = get_admin_client()
    resultado = {"tecnicos_creados": 0, "agenda_agregada": 0, "error": None}

    try:
        usuarios = (
            get_reader()
            .table("usuarios")
            .select("id,empleado_id,activo")
            .eq("rol", "TECNICO")
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        resultado["error"] = f"No se pudieron leer los usuarios TECNICO: {exc}"
        log.error(resultado["error"])
        return resultado

    try:
        existentes = (
            get_reader()
            .table("tecnicos")
            .select("id,empleado_id")
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        resultado["error"] = f"No se pudo leer la tabla tecnicos: {exc}"
        log.error(resultado["error"])
        return resultado

    por_empleado = {t["empleado_id"]: t["id"] for t in existentes if t.get("empleado_id")}

    for u in usuarios:
        emp_id = u.get("empleado_id")
        if not emp_id or u.get("activo") is False:
            continue

        tecnico_id = por_empleado.get(emp_id)
        if tecnico_id is None:
            try:
                resp = db.from_("tecnicos").insert(
                    {"empleado_id": emp_id, "especialidad": "Electrodomésticos"}
                ).execute()
                tecnico_id = (resp.data or [{}])[0].get("id")
                if tecnico_id:
                    por_empleado[emp_id] = tecnico_id
                    resultado["tecnicos_creados"] += 1
            except Exception as exc:  # noqa: BLE001
                resultado["error"] = f"Técnico empleado {emp_id}: {exc}"
                log.error(resultado["error"])
                continue

        if not tecnico_id:
            continue

        hoy = _fecha_hoy()
        for delta in range(1, 8):
            dia = hoy + timedelta(days=delta)
            if dia.weekday() == 6:  # domingo = descanso
                continue
            fecha = dia.isoformat()
            try:
                ya_existe = (
                    get_reader()
                    .table("agenda_tecnicos")
                    .select("id")
                    .eq("tecnico_id", tecnico_id)
                    .eq("fecha", fecha)
                    .limit(1)
                    .execute()
                    .data
                    or []
                )
            except Exception as exc:  # noqa: BLE001
                resultado["error"] = f"Agenda {fecha}: {exc}"
                log.error(resultado["error"])
                continue
            if ya_existe:
                continue
            try:
                db.from_("agenda_tecnicos").insert(
                    {
                        "tecnico_id": tecnico_id,
                        "fecha": fecha,
                        "hora_inicio": "09:00",
                        "hora_fin": "18:00",
                        "disponible": True,
                    }
                ).execute()
                resultado["agenda_agregada"] += 1
            except Exception as exc:  # noqa: BLE001
                resultado["error"] = f"Franja {tecnico_id}/{fecha}: {exc}"
                log.error(resultado["error"])

    return resultado


def _normalizar(datos: dict) -> dict:
    def _v(campo):
        return (datos.get(campo) or "").strip()

    tipo_atencion = _v("tipo_atencion").upper()

    distrito = _v("distrito")
    calle = _v("calle")
    if distrito and calle:
        direccion = f"{distrito}{SEPARADOR_DIRECCION}{calle}"
    else:
        direccion = distrito or calle

    tipo_id = datos.get("tipo_id") or datos.get("tipo_id_alt") or ""
    try:
        tipo_id_val = int(tipo_id) if str(tipo_id).strip() else 0
    except (TypeError, ValueError):
        tipo_id_val = 0

    marca_id = datos.get("marca_id") or datos.get("marca_id_alt") or ""
    try:
        marca_id_val = int(marca_id) if str(marca_id).strip() else 0
    except (TypeError, ValueError):
        marca_id_val = 0

    return {
        "tipo_documento_id": int(datos.get("tipo_documento_id") or 1),
        "numero_documento": _v("numero_documento"),
        "nombres": _v("nombres"),
        "apellidos": _v("apellidos"),
        "razon_social": _v("razon_social"),
        "telefono": _v("telefono"),
        "correo": _v("correo"),
        "tipo_id": tipo_id_val,
        "marca_id": marca_id_val,
        "tipo_otro": _v("tipo_otro"),
        "marca_otro": _v("marca_otro"),
        "modelo": _v("modelo"),
        "numero_serie": _v("numero_serie"),
        "color": _v("color"),
        "tipo_atencion": tipo_atencion,
        "fecha_reserva": _v("fecha_reserva"),
        "hora_inicio": _v("hora_inicio"),
        "motivo": _v("motivo"),
        "observaciones": _v("observaciones"),
        "distrito": distrito,
        "calle": direccion,
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
        "tipo_id": d["tipo_id"] or None,
        "marca_id": d["marca_id"] or None,
        "modelo": d["modelo"] or None,
        "numero_serie": d["numero_serie"] or None,
        "color": d["color"] or None,
        "observaciones": d["observaciones"] or None,
        "estado": ESTADO_ELECTRODOMESTICO,
    }
    tipo_otro = (d.get("tipo_otro") or "").strip()
    marca_otro = (d.get("marca_otro") or "").strip()
    if tipo_otro:
        fila["tipo_otro"] = tipo_otro[:30]
    if marca_otro:
        fila["marca_otro"] = marca_otro[:30]
    try:
        resp = db.from_("electrodomesticos").insert(fila).execute()
        return (resp.data or [{}])[0].get("id")
    except Exception:
        fila.pop("tipo_otro", None)
        fila.pop("marca_otro", None)
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
    if d.get("distrito"):
        fila["distrito"] = d["distrito"]
    try:
        resp = db.from_("reservas").insert(fila).execute()
        return (resp.data or [{}])[0].get("id")
    except Exception:
        # La migración sql/04_reservas_distrito.sql aún no se aplicó: se guarda
        # igual (el distrito queda dentro de `calle`).
        if "distrito" not in fila:
            raise
        fila.pop("distrito", None)
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


def marcar_primera_atencion(reserva_id) -> bool:
    """Sella la hora de la primera atención de la cita (soporte del KPI-01/02).

    Se escribe una sola vez: el UPDATE filtra por `fecha_primera_atencion IS
    NULL`, así que volver a llamarlo no pisa la marca original. Por eso el tiempo
    de respuesta se mide contra el primer contacto, no contra el último.

    Es idempotente y nunca rompe el flujo: si Supabase falla, la cita se sigue
    gestionando y el KPI solo pierde ese dato.
    """
    if not reserva_id:
        return False
    try:
        resp = (
            get_admin_client()
            .table("reservas")
            .update({"fecha_primera_atencion": _ahora()})
            .eq("id", reserva_id)
            .is_("fecha_primera_atencion", None)
            .execute()
        )
        return bool(resp.data)
    except Exception:  # noqa: BLE001
        log.warning("No se pudo marcar la primera atención de la reserva %s", reserva_id)
        return False