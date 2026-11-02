"""Reglas de tiempo y vigencia de la disponibilidad de los técnicos (agenda_tecnicos).

Toda comparación de fecha/hora del negocio se hace en America/Lima. El servidor y la
base de datos corren en UTC, así que `date.today()` / `current_date` NO sirven: entre
las 19:00 y las 24:00 de Lima ya marcan el día siguiente.

La base de datos aplica las mismas reglas con triggers (sql/09_agenda_vigencia.sql);
este módulo las repite para dar mensajes claros antes de llegar a la base.
"""

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    TZ_LIMA = ZoneInfo("America/Lima")
except Exception:  # noqa: BLE001  (sin base tzdata, p. ej. Windows): Perú no usa horario de verano
    TZ_LIMA = timezone(timedelta(hours=-5))

ESTADO_ACTIVA = "ACTIVA"
ESTADO_VENCIDA = "VENCIDA"
ESTADO_INACTIVA = "INACTIVA"  # solo de presentación: sesión vigente que el técnico desactivó

ANIO_MINIMO = 2020  # mismo límite que ck_agenda_fecha_razonable en la base

_RE_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def ahora_lima() -> datetime:
    return datetime.now(TZ_LIMA)


def hoy_lima() -> date:
    return ahora_lima().date()


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def validar_franja(fecha: str, hora_inicio: str, hora_fin: str, ahora: Optional[datetime] = None) -> Optional[str]:
    """Valida una sesión de disponibilidad. Devuelve el mensaje de error en español o None si es válida."""
    ahora = ahora or ahora_lima()
    fecha = (fecha or "").strip()
    hora_inicio = (hora_inicio or "").strip()
    hora_fin = (hora_fin or "").strip()

    if not fecha:
        return "Indica la fecha de la sesión."
    try:
        dia = date.fromisoformat(fecha)
    except ValueError:
        return "La fecha no es válida."
    if not _RE_HORA.match(hora_inicio) or not _RE_HORA.match(hora_fin):
        return "Indica una hora de inicio y una de fin válidas (formato HH:MM)."
    if dia.year < ANIO_MINIMO:
        return f"La fecha {_fmt(dia)} no es válida; revisa el año."
    if dia < ahora.date():
        return f"No puedes registrar disponibilidad en una fecha anterior a hoy ({_fmt(dia)})."
    if dia > ahora.date():
        return (
            f"Solo puedes registrar disponibilidad para hoy ({_fmt(ahora.date())}); "
            f"no se permiten fechas posteriores ({_fmt(dia)})."
        )
    if hora_fin <= hora_inicio:
        return "La hora de fin debe ser posterior a la hora de inicio."
    if dia == ahora.date() and hora_inicio < ahora.strftime("%H:%M"):
        return f"La hora de inicio ({hora_inicio}) ya pasó; ahora son las {ahora.strftime('%H:%M')}. Elige una hora posterior."
    return None


def sesion_terminada(fecha: str, hora_fin: str, ahora: Optional[datetime] = None) -> bool:
    """True si la fecha/hora de fin de la sesión ya pasó (hora de Lima)."""
    ahora = ahora or ahora_lima()
    try:
        dia = date.fromisoformat((fecha or "")[:10])
    except ValueError:
        return False
    if dia < ahora.date():
        return True
    return dia == ahora.date() and (hora_fin or "")[:5] <= ahora.strftime("%H:%M")


def estado_efectivo(fecha: str, hora_fin: str, disponible, estado: Optional[str] = None,
                    ahora: Optional[datetime] = None) -> str:
    """VENCIDA / ACTIVA / INACTIVA tal como debe verlo el usuario.

    Se calcula también con la fecha/hora: así una sesión que terminó hace un minuto
    figura como vencida aunque el proceso programado todavía no la haya cerrado.
    """
    if estado == ESTADO_VENCIDA or sesion_terminada(fecha, hora_fin, ahora):
        return ESTADO_VENCIDA
    return ESTADO_ACTIVA if disponible is not False else ESTADO_INACTIVA


def franja_vigente(franja: dict, ahora: Optional[datetime] = None) -> bool:
    """True si la franja se puede ofrecer para reservar: activa, marcada disponible y sin terminar."""
    return estado_efectivo(
        franja.get("fecha") or "", franja.get("hora_fin") or "", franja.get("disponible"),
        franja.get("estado"), ahora,
    ) == ESTADO_ACTIVA


def mensaje_error_bd(exc: Exception, por_defecto: str) -> str:
    """Traduce los errores de los triggers/restricciones de la base a un mensaje claro."""
    texto = str(exc)
    for marca in (
        "No se puede registrar disponibilidad con una fecha anterior a hoy",
        "Solo se puede registrar disponibilidad para el día de hoy",
        "ya pasó; en Lima son las",
        "no se puede reactivar",
        "ya no se puede modificar",
    ):
        if marca in texto:
            m = re.search(r"'message':\s*'([^']*)'", texto) or re.search(r'"message":\s*"([^"]*)"', texto)
            return m.group(1) if m else por_defecto
    if "23505" in texto or "duplicate key" in texto.lower():
        return "Ya existe una sesión con esa misma fecha y hora de inicio."
    return por_defecto


_ultimo_cierre = 0.0


def cerrar_vencidas(minimo_segundos: int = 300, forzar: bool = False) -> int:
    """Marca como VENCIDA lo que ya terminó (red de seguridad del job de pg_cron).

    Se invoca al abrir la pantalla de disponibilidad, como mucho una vez cada
    `minimo_segundos`. Nunca lanza errores: la vigencia ya se calcula al leer.
    """
    global _ultimo_cierre
    ahora = time.monotonic()
    if not forzar and ahora - _ultimo_cierre < minimo_segundos:
        return 0
    _ultimo_cierre = ahora
    try:
        from app.supabase_client import get_admin_client

        resp = get_admin_client().rpc("cerrar_sesiones_vencidas").execute()
        return int(resp.data or 0)
    except Exception:  # noqa: BLE001
        log.debug("No se pudo ejecutar cerrar_sesiones_vencidas", exc_info=True)
        return 0
