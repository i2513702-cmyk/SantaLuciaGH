"""Registro de visitas al sitio web (soporte del KPI de conversión).

`visitas_web` ya existe en Supabase con `sesion_id`, `fecha_visita`, `ruta` y
`endpoint`. Aqui se escribe una fila por sesión y nada más, para que el KPI-03
pueda contar "sesiones que visitaron y comprar" sin duplicar.

La clave de sesión es la MISMA que usa el carrito (`carrito_service` /
`routes/carrito.py`): `user:<usuario_id>` si hay sesión iniciada y
`anon:<token>` si es una visita anónima. Gracias a eso una visita y su compra
se pueden unir sin inventar identificadores.
"""

import logging
from datetime import datetime, timezone

from flask import request, session

from app.decorators import es_administrativo
from app.services import carrito_service
from app.supabase_client import get_admin_client, get_reader

log = logging.getLogger(__name__)

#: Rutas que cuentan como visita del catalogo web.
RUTAS_SEGUIDAS = ("/", "/tienda", "/carrito")


def clave_sesion() -> str:
    """Clave de sesión compartida por el tracking y el carrito."""
    if session.get("usuario_id"):
        return f"user:{session['usuario_id']}"
    token = session.get("carrito_sesion")
    if not token:
        token = carrito_service.nueva_sesion()
        session["carrito_sesion"] = token
    return f"anon:{token}"


def _ya_registrada(clave: str) -> bool:
    """True si esta sesión de navegador ya tiene su fila en `visitas_web`."""
    try:
        resp = (
            get_reader()
            .table("visitas_web")
            .select("id")
            .eq("sesion_id", clave)
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception:  # noqa: BLE001
        log.exception("Tracking: no se pudo consultar la visita previa")
        return False


def registrar_visita(ruta: str | None = None) -> bool:
    """Guarda una fila en `visitas_web` por sesión. Nunca rompe la navegación.

    - Solo cuenta la primera visita de cada sesión (una fila por `sesion_id`).
    - El administrador no se cuenta: gestiona el sistema y no puede tener
      carrito, así que su visita al catálogo solo bajaría la tasa de conversión.
    - Si la escritura falla, la página sigue cargando: el KPI pierde un dato,
      no el sitio.
    """
    ruta = ruta or (request.path if request else "/")
    if ruta not in RUTAS_SEGUIDAS:
        return False
    if es_administrativo(session.get("rol")):
        return False
    if session.get("visita_registrada"):
        return False

    clave = clave_sesion()
    if not _ya_registrada(clave):
        try:
            get_admin_client().table("visitas_web").insert(
                {
                    "sesion_id": clave,
                    "fecha_visita": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "ruta": ruta,
                    "endpoint": request.endpoint if request else None,
                }
            ).execute()
        except Exception:  # noqa: BLE001
            log.exception("Tracking: no se pudo registrar la visita de %s", clave)
            # No se marca la sesión como registrada: se reintentará al navegar.
            return False
    session["visita_registrada"] = True
    return True
