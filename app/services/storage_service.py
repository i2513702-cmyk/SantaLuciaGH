"""Subida de imágenes a Supabase Storage (catálogo de productos y marca).

Los archivos se guardan en buckets públicos y se enlazan por URL pública:

- bucket `productos` -> imágenes de productos (URL guardada en `imagen_url`).
- bucket `logos`     -> marca de Santa Lucía:
    - `logo.<ext>`   -> logo (barra superior, pie y login).
    - `footer.<ext>` -> imagen de fondo del pie de página.

Sin dependencias nuevas: usamos el cliente de supabase-py que ya existe.
"""

import logging
import uuid

from app.config import Config
from app.exceptions import ValidationError
from app.supabase_client import get_admin_client

log = logging.getLogger(__name__)

BUCKET_PRODUCTOS = "productos"
BUCKET_LOGOS = "logos"
PREFIJO_LOGO = "logo."
PREFIJO_FONDO = "footer."

EXTENSIONES = {"png", "jpg", "jpeg", "webp", "svg"}
CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}

# Piezas de marca con nombre estable (para poder sobrescribirlas).
_PIEZAS = {"logo": PREFIJO_LOGO, "fondo": PREFIJO_FONDO}

# Estado por pieza: {"revisado": bool, "archivo": str|None, "url": str|None}
_estado = {clave: {"revisado": False, "archivo": None, "url": None} for clave in _PIEZAS}


def _bucket(bucket: str):
    return get_admin_client().storage.from_(bucket)


def asegurar_bucket(bucket: str) -> None:
    """Crea el bucket público si no existe (idempotente)."""
    try:
        get_admin_client().storage.create_bucket(bucket, {"public": True})
    except Exception as exc:  # noqa: BLE001 - ya existe o permiso; no romper
        if "already" not in str(exc).lower():
            log.debug("No se pudo asegurar el bucket %s: %s", bucket, exc)


def _validar_archivo(archivo) -> tuple:
    """Valida el archivo recibido y devuelve (extensión, bytes)."""
    nombre = (getattr(archivo, "filename", "") or "").strip()
    if not nombre:
        raise ValidationError("Selecciona un archivo de imagen.")
    ext = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    if ext not in EXTENSIONES:
        raise ValidationError("Formato no permitido: usa PNG, JPG, JPEG, WEBP o SVG.")
    datos = archivo.read()
    if not datos:
        raise ValidationError("El archivo está vacío.")
    if len(datos) > Config.MAX_CONTENT_LENGTH:
        raise ValidationError("La imagen supera el límite de 2 MB.")
    return ext, datos


def url_publica(bucket: str, ruta: str) -> str:
    base = Config.SUPABASE_URL.rstrip("/")
    return f"{base}/storage/v1/object/public/{bucket}/{ruta}"


def subir_imagen(bucket: str, ruta_base: str, archivo, upsert: bool = False) -> tuple:
    """Sube una imagen al bucket y devuelve (url_pública, ruta_final)."""
    ext, datos = _validar_archivo(archivo)
    asegurar_bucket(bucket)
    ruta = f"{ruta_base}.{ext}"
    opciones = {"content-type": CONTENT_TYPES.get(ext, "application/octet-stream")}
    if upsert:
        # storage3 espera el valor como string (lo envía tal cual en x-upsert).
        opciones["upsert"] = "true"
    _bucket(bucket).upload(ruta, datos, opciones)
    return url_publica(bucket, ruta), ruta


def subir_imagen_producto(archivo, producto_id: int) -> str:
    """Sube la imagen de un producto y devuelve su URL pública."""
    ruta_base = f"producto-{producto_id}-{uuid.uuid4().hex[:8]}"
    url, _ = subir_imagen(BUCKET_PRODUCTOS, ruta_base, archivo)
    return url


# --------------------------------------------------------------------------
# Marca: logo y fondo del pie de página (bucket `logos`)
# --------------------------------------------------------------------------

def _listar_bucket() -> list:
    try:
        return _bucket(BUCKET_LOGOS).list("", {"limit": 50}) or []
    except Exception:  # noqa: BLE001
        log.debug("No se pudo listar el bucket %s", BUCKET_LOGOS, exc_info=True)
        return []


def _descubrir(prefijo: str) -> str | None:
    """Devuelve el nombre del archivo que empieza con `prefijo`, si existe."""
    for item in _listar_bucket():
        nombre = item.get("name") or ""
        if nombre.startswith(prefijo):
            return nombre
    return None


def _url_pieza(clave: str) -> str | None:
    """URL pública de una pieza de marca; se descubre una sola vez."""
    info = _estado[clave]
    if not info["revisado"]:
        info["archivo"] = _descubrir(_PIEZAS[clave])
        info["url"] = (
            url_publica(BUCKET_LOGOS, info["archivo"]) if info["archivo"] else None
        )
        info["revisado"] = True
    return info["url"]


def _subir_pieza(clave: str, archivo) -> str:
    """Sobrescribe una pieza de marca (nombre estable) y devuelve su URL."""
    prefijo = _PIEZAS[clave]
    url, ruta = subir_imagen(BUCKET_LOGOS, prefijo.rstrip("."), archivo, upsert=True)

    # Limpia versiones viejas con otra extensión (logo.png vs logo.jpg…).
    for item in _listar_bucket():
        nombre = item.get("name") or ""
        if nombre.startswith(prefijo) and nombre != ruta:
            try:
                _bucket(BUCKET_LOGOS).remove([nombre])
            except Exception:  # noqa: BLE001
                pass

    _estado[clave] = {"revisado": True, "archivo": ruta, "url": url}
    return url


def _quitar_pieza(clave: str) -> None:
    """Elimina una pieza de marca subida (vuelve al valor por defecto)."""
    info = _estado[clave]
    archivo = info["archivo"] or _descubrir(_PIEZAS[clave])
    if archivo:
        try:
            _bucket(BUCKET_LOGOS).remove([archivo])
        except Exception:  # noqa: BLE001
            pass
    _estado[clave] = {"revisado": True, "archivo": None, "url": None}


# --- API pública: logo -----------------------------------------------------

def obtener_logo_url() -> str | None:
    """URL pública del logo si ya se subió uno; None si se usa el estático."""
    return _url_pieza("logo")


def subir_logo(archivo) -> str:
    """Sobrescribe el logo en storage y devuelve su URL pública."""
    return _subir_pieza("logo", archivo)


def quitar_logo() -> None:
    """Elimina el logo subido; se vuelve al logo estático por defecto."""
    _quitar_pieza("logo")


# --- API pública: fondo del pie de página ----------------------------------

def obtener_fondo_url() -> str | None:
    """URL pública del fondo del pie si ya se subió uno; None si no hay."""
    return _url_pieza("fondo")


def subir_fondo(archivo) -> str:
    """Sobrescribe el fondo del pie de página y devuelve su URL pública."""
    return _subir_pieza("fondo", archivo)


def quitar_fondo() -> None:
    """Elimina el fondo del pie subido; se vuelve al color por defecto."""
    _quitar_pieza("fondo")