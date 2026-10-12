"""Control de intentos de login por IP.

Politica (configurable en app/config.py):
  - A los LOGIN_MAX_INTENTOS fallos -> espera de LOGIN_ESPERA_MIN minutos.
  - Pasada la espera, LOGIN_FALLOS_BLOQUEO fallos mas -> la IP queda bloqueada
    (`bloqueada = true`) hasta que un administrador la desbloquee.
Persistencia en la tabla `ip_acceso` (docs/sql/001_seguridad_login.sql). Si la
tabla aun no existe se usa memoria del proceso para no dejar el login abierto.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from app.config import Config
from app.supabase_client import get_admin_client, get_reader

log = logging.getLogger(__name__)
TABLA = "ip_acceso"
UTC = timezone.utc
_memoria: dict[str, dict] = {}
_lock = threading.Lock()


def _ahora() -> datetime:
    return datetime.now(UTC)


def _dt(valor):
    if not valor:
        return None
    try:
        d = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _vacia(ip: str) -> dict:
    return {"ip": ip, "intentos_fallidos": 0, "bloqueada": False, "bloqueo_hasta": None}


def _leer(ip: str) -> dict:
    try:
        filas = get_reader().table(TABLA).select("*").eq("ip", ip).limit(1).execute().data
        return filas[0] if filas else _vacia(ip)
    except Exception:  # noqa: BLE001
        log.warning("login_guard: tabla %s no disponible, se usa memoria", TABLA)
        with _lock:
            return dict(_memoria.get(ip) or _vacia(ip))


def _guardar(fila: dict) -> None:
    try:
        get_admin_client().table(TABLA).upsert(fila, on_conflict="ip").execute()
    except Exception:  # noqa: BLE001
        with _lock:
            _memoria[fila["ip"]] = dict(fila)


def estado(ip: str) -> dict:
    """{'bloqueada': bool, 'espera_segundos': int, 'intentos': int}."""
    fila = _leer(ip)
    espera = 0
    hasta = _dt(fila.get("bloqueo_hasta"))
    if hasta and hasta > _ahora():
        espera = int((hasta - _ahora()).total_seconds()) + 1
    return {
        "bloqueada": bool(fila.get("bloqueada")),
        "espera_segundos": espera,
        "intentos": int(fila.get("intentos_fallidos") or 0),
    }


def mensaje_bloqueo(est: dict) -> str | None:
    if est["bloqueada"]:
        return "Tu IP fue bloqueada por demasiados intentos fallidos. Contacta al administrador."
    if est["espera_segundos"]:
        minutos, seg = divmod(est["espera_segundos"], 60)
        return f"Demasiados intentos. Espera {minutos}:{seg:02d} min antes de volver a intentar."
    return None


def registrar_fallo(ip: str, usuario: str = "") -> str:
    """Suma un fallo, aplica espera/bloqueo y devuelve el aviso para el usuario."""
    fila = _leer(ip)
    n = int(fila.get("intentos_fallidos") or 0) + 1
    tope = Config.LOGIN_MAX_INTENTOS + Config.LOGIN_FALLOS_BLOQUEO
    ahora = _ahora()
    fila.update(
        intentos_fallidos=n,
        ultimo_usuario=(usuario or "")[:80],
        ultimo_intento=ahora.isoformat(),
    )
    if n >= tope:
        fila.update(bloqueada=True, fecha_bloqueo=ahora.isoformat(), bloqueo_hasta=None)
        aviso = "Tu IP fue bloqueada por demasiados intentos fallidos. Contacta al administrador."
    elif n == Config.LOGIN_MAX_INTENTOS:
        fila["bloqueo_hasta"] = (ahora + timedelta(minutes=Config.LOGIN_ESPERA_MIN)).isoformat()
        aviso = f"Alcanzaste {n} intentos fallidos. Espera {Config.LOGIN_ESPERA_MIN} minutos."
    elif n < Config.LOGIN_MAX_INTENTOS:
        aviso = f"Usuario o contraseña incorrectos. Te quedan {Config.LOGIN_MAX_INTENTOS - n} intento(s)."
    else:
        aviso = (
            f"Usuario o contraseña incorrectos. Te quedan {tope - n} intento(s) "
            "antes de que tu IP sea bloqueada."
        )
    _guardar(fila)
    return aviso


def registrar_exito(ip: str) -> None:
    fila = _leer(ip)
    if fila.get("bloqueada") or not fila.get("intentos_fallidos"):
        return
    fila.update(intentos_fallidos=0, bloqueo_hasta=None)
    _guardar(fila)


def listar() -> list[dict]:
    """IPs con actividad sospechosa (con fallos o bloqueadas), bloqueadas primero."""
    try:
        filas = get_reader().table(TABLA).select("*").order("ultimo_intento", desc=True).execute().data or []
    except Exception:  # noqa: BLE001
        with _lock:
            filas = [dict(f) for f in _memoria.values()]
    filas = [f for f in filas if f.get("bloqueada") or f.get("intentos_fallidos")]
    filas.sort(key=lambda f: not f.get("bloqueada"))
    return filas


def desbloquear(ip: str, admin: str) -> None:
    fila = _leer(ip)
    fila.update(
        bloqueada=False,
        intentos_fallidos=0,
        bloqueo_hasta=None,
        desbloqueada_por=admin,
        fecha_desbloqueo=_ahora().isoformat(),
    )
    _guardar(fila)


def bloquear(ip: str, admin: str) -> None:
    fila = _leer(ip)
    fila.update(bloqueada=True, fecha_bloqueo=_ahora().isoformat(), bloqueo_hasta=None)
    fila["ultimo_usuario"] = f"manual:{admin}"[:80]
    _guardar(fila)
