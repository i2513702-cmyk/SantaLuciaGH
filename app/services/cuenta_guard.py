"""Bloqueo temporal por cuenta (complementa el bloqueo por IP de login_guard).

Politica (configurable en app/config.py):
  - A los CUENTA_MAX_INTENTOS fallos seguidos la cuenta queda bloqueada
    CUENTA_BLOQUEO_MIN minutos (`usuarios.bloqueo_hasta`). El contador vuelve a 0.
  - El bloqueo es TEMPORAL a proposito: uno permanente dejaria que cualquiera
    bloquee a otro usuario fallando su clave a proposito.
  - Un administrador puede liberarla antes desde /admin/seguridad o con
    `flask cuenta:desbloquear <usuario>`.

Columnas en `usuarios`: intentos_fallidos, bloqueo_hasta, ultimo_fallo
(docs/sql/002_seguridad_historial_cuentas.sql). Si aun no existen, nada de esto
rompe el login: se registra una advertencia y la cuenta no se bloquea.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import Config
from app.services import login_guard
from app.supabase_client import get_admin_client, get_reader

log = logging.getLogger(__name__)
TABLA = "usuarios"
VISTA = "v_cuentas_en_riesgo"
UTC = timezone.utc


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


def segundos_de_espera(usuario: dict) -> int:
    """Segundos que faltan para que se libere la cuenta (0 si no esta bloqueada)."""
    hasta = _dt(usuario.get("bloqueo_hasta"))
    if hasta and hasta > _ahora():
        return int((hasta - _ahora()).total_seconds()) + 1
    return 0


def mensaje_bloqueo(segundos: int) -> str:
    minutos = max(1, -(-segundos // 60))  # redondeo hacia arriba
    return (
        "Esta cuenta está bloqueada temporalmente por demasiados intentos fallidos. "
        f"Vuelve a intentar en {minutos} min o contacta al administrador."
    )


def _actualizar(usuario_id, campos: dict) -> bool:
    try:
        get_admin_client().table(TABLA).update(campos).eq("id", usuario_id).execute()
        return True
    except Exception:  # noqa: BLE001
        log.warning("cuenta_guard: no se pudo actualizar usuarios (¿falta el SQL 002?)")
        return False


def registrar_fallo(usuario: dict, ip: str = "") -> bool:
    """Suma un fallo a la cuenta. Devuelve True si con este fallo quedo bloqueada."""
    n = int(usuario.get("intentos_fallidos") or 0) + 1
    ahora = _ahora()
    nombre = usuario.get("nombre_usuario") or ""
    if n >= Config.CUENTA_MAX_INTENTOS:
        hasta = ahora + timedelta(minutes=Config.CUENTA_BLOQUEO_MIN)
        _actualizar(
            usuario["id"],
            {"intentos_fallidos": 0, "bloqueo_hasta": hasta.isoformat(), "ultimo_fallo": ahora.isoformat()},
        )
        login_guard.registrar_evento(
            "BLOQUEO_CUENTA_AUTO",
            ip=ip,
            usuario=nombre,
            detalle=f"{Config.CUENTA_MAX_INTENTOS} fallos; bloqueada {Config.CUENTA_BLOQUEO_MIN} min",
        )
        return True
    _actualizar(usuario["id"], {"intentos_fallidos": n, "ultimo_fallo": ahora.isoformat()})
    return False


def registrar_exito(usuario: dict) -> None:
    """Login correcto: limpia contador y bloqueo vencido (solo escribe si hacia falta)."""
    if usuario.get("intentos_fallidos") or usuario.get("bloqueo_hasta"):
        _actualizar(usuario["id"], {"intentos_fallidos": 0, "bloqueo_hasta": None})


def listar() -> list[dict]:
    """Cuentas con fallos o bloqueadas ahora (vista v_cuentas_en_riesgo)."""
    try:
        filas = get_reader().table(VISTA).select("*").execute().data or []
    except Exception:  # noqa: BLE001
        log.warning("cuenta_guard: vista %s no disponible", VISTA)
        return []
    filas.sort(key=lambda f: not f.get("bloqueada_ahora"))
    return filas


def desbloquear(usuario_id, admin: str, nombre: str = "") -> bool:
    ok = _actualizar(usuario_id, {"intentos_fallidos": 0, "bloqueo_hasta": None})
    if ok:
        login_guard.registrar_evento("DESBLOQUEO_CUENTA", usuario=nombre, admin=admin)
    return ok
