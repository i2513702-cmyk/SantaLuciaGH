"""Subida de imágenes a Supabase Storage (catálogo de productos y marca/logo).

Los archivos se guardan en buckets públicos y se enlazan por URL pública:

- bucket `productos` -> imágenes de productos (URL guardada en `imagen_url`).
- bucket `logos`    -> logo de Santa Lucía (archivo `logo.<ext>` con nombre
  estable para poder sobrescribirlo al cambiar el logo).

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

EXTENSIONES = {"png", "jpg", "jpeg", "webp", "svg"}
CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}

# Logo: se descubre una sola vez (la subida lo invalida).
_logo_revisado = False
_logo_archivo: str | None = None
_logo_url: str | None = None


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
# Logo de Santa Lucía
# --------------------------------------------------------------------------

def _descubrir_logo() -> str | None:
    """Busca en el bucket logos un archivo `logo.*` y devuelve su ruta."""
    try:
        items = _bucket(BUCKET_LOGOS).list("", {"limit": 20})
        for item in items or []:
            nombre = item.get("name") or ""
            if nombre.startswith(PREFIJO_LOGO):
                return nombre
    except Exception:  # noqa: BLE001
        log.debug("No se pudo revisar el logo en storage", exc_info=True)
    return None


def obtener_logo_url() -> str | None:
    """URL pública del logo si ya se subió uno; None si se usa el estático."""
    global _logo_revisado, _logo_archivo, _logo_url
    if not _logo_revisado:
        _logo_revisado = True
        _logo_archivo = _descubrir_logo()
        _logo_url = (
            url_publica(BUCKET_LOGOS, _logo_archivo) if _logo_archivo else None
        )
    return _logo_url


def subir_logo(archivo) -> str:
    """Sobrescribe el logo en storage y devuelve su URL pública."""
    global _logo_revisado, _logo_archivo, _logo_url

    # Cualquier extensión permitida se guarda con prefijo estable `logo.`
    url, ruta = subir_imagen(BUCKET_LOGOS, "logo", archivo, upsert=True)

    # Limpia logos viejos con otra extensión para no acumular `logo.png`,
    # `logo.jpg`, etc.
    try:
        for item in _bucket(BUCKET_LOGOS).list("", {"limit": 20}) or []:
            nombre = item.get("name") or ""
            if nombre.startswith(PREFIJO_LOGO) and nombre != ruta:
                _bucket(BUCKET_LOGOS).remove([nombre])
    except Exception:  # noqa: BLE001
        pass

    _logo_revisado = True
    _logo_archivo = ruta
    _logo_url = url
    return url


def quitar_logo() -> None:
    """Elimina el logo subido; se vuelve al logo estático por defecto."""
    global _logo_revisado, _logo_archivo, _logo_url
    try:
        if _logo_archivo:
            _bucket(BUCKET_LOGOS).remove([_logo_archivo])
    except Exception:  # noqa: BLE001
        pass
    _logo_revisado = True
    _logo_archivo = None
    _logo_url = None