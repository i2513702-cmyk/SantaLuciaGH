"""Zona horaria de la aplicacion (Lima, UTC-5)."""
from datetime import datetime, timedelta, timezone

LIMA_TZ = timezone(timedelta(hours=-5))


def ahora_lima() -> datetime:
    return datetime.now(LIMA_TZ)


def hoy_lima():
    return ahora_lima().date()
