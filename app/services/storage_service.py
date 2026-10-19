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
BUCKET_AVATARES = "avatares"
PREFIJO_LOGO = "logo."
PREFIJO_FONDO = "footer."
# Foto de perfil: `usuario-<id>.<ext>` (nombre estable por usuario).
PREFIJO_AVATAR = "usuario-"

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
        get_admin_client().storage.create_bucket(bucket, options={"public": True})
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
    """Sube una foto del producto y devuelve su URL pública.

    Cada subida crea un archivo nuevo con el prefijo `producto-<id>-`, así que
    un producto puede acumular varias fotos (distintos ángulos).
    """
    ruta_base = f"producto-{producto_id}-{uuid.uuid4().hex[:8]}"
    url, ruta = subir_imagen(BUCKET_PRODUCTOS, ruta_base, archivo)
    _cargar_fotos()
    try:
        pid = int(producto_id)
        _fotos.setdefault(pid, []).append(ruta)
        _fotos[pid].sort()
    except (TypeError, ValueError):
        pass
    return url


# --------------------------------------------------------------------------
# Fotos de producto: varias por producto (bucket `productos`)
#
# Se descubren listando el bucket por el prefijo `producto-<id>-`, igual que
# el logo o los avatares. Así no hace falta añadir una columna nueva a la
# tabla `productos`. La imagen de la columna `imagen_url` es la principal.
# --------------------------------------------------------------------------

_fotos: dict = {}            # producto_id -> [nombres de archivo, en orden]
_fotos_cargado = False


def _id_desde_foto(nombre: str):
    """Extrae el id de producto de un nombre `producto-<id>-<aleatorio>.<ext>`."""
    if not nombre or not nombre.startswith("producto-"):
        return None
    resto = nombre[len("producto-"):]
    numero = resto.split("-", 1)[0]
    return int(numero) if numero.isdigit() else None


def _cargar_fotos() -> None:
    """Lista el bucket de productos una sola vez y agrupa los archivos por id."""
    global _fotos_cargado
    if _fotos_cargado:
        return
    try:
        items = _bucket(BUCKET_PRODUCTOS).list("", {"limit": 1000}) or []
    except Exception:  # noqa: BLE001
        log.debug("No se pudo listar el bucket %s", BUCKET_PRODUCTOS, exc_info=True)
        items = []
    for item in items:
        nombre = item.get("name") or ""
        pid = _id_desde_foto(nombre)
        if pid is not None:
            _fotos.setdefault(pid, []).append(nombre)
    for nombres in _fotos.values():
        nombres.sort()
    _fotos_cargado = True


def listar_fotos_producto(producto_id, principal: str | None = None) -> list:
    """Fotos de un producto como [{"nombre": str|None, "url": str}].

    La imagen principal (columna `imagen_url`) se coloca primero.
    """
    try:
        pid = int(producto_id)
    except (TypeError, ValueError):
        return []
    _cargar_fotos()
    fotos = [
        {"nombre": nombre, "url": url_publica(BUCKET_PRODUCTOS, nombre)}
        for nombre in _fotos.get(pid, [])
    ]
    if principal:
        for i, foto in enumerate(fotos):
            if foto["url"] == principal:
                fotos.insert(0, fotos.pop(i))
                break
        else:
            fotos.insert(0, {"nombre": None, "url": principal})
    return fotos


def quitar_fotos_producto(producto_id, nombres) -> int:
    """Elimina las fotos indicadas del producto y devuelve cuántas borró.

    Solo elimina nombres que pertenezcan a ese producto (mismo prefijo), para
    no borrar archivos de otros productos.
    """
    try:
        pid = int(producto_id)
    except (TypeError, ValueError):
        return 0
    prefijo = f"producto-{pid}-"
    a_borrar = [
        nombre
        for nombre in (nombres or [])
        if nombre and nombre.startswith(prefijo) and "/" not in nombre
    ]
    if not a_borrar:
        return 0
    try:
        _bucket(BUCKET_PRODUCTOS).remove(a_borrar)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"No se pudieron eliminar algunas fotos: {exc}") from exc

    _cargar_fotos()
    restantes = [n for n in _fotos.get(pid, []) if n not in a_borrar]
    _fotos[pid] = restantes
    return len(a_borrar)


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


# --------------------------------------------------------------------------
# Foto de perfil de usuario (bucket `avatares`)
#
# Se guarda como `usuario-<id>.<ext>` (nombre estable por usuario) y se
# descubre listando el bucket, igual que el logo. Así no hace falta añadir una
# columna nueva a la tabla `usuarios`.
# --------------------------------------------------------------------------

_avatares: dict = {}          # usuario_id -> nombre de archivo en el bucket
_avatares_cargado = False


def _id_desde_avatar(nombre: str):
    """Extrae el id de usuario de un nombre `usuario-<id>.<ext>` (o None)."""
    if not nombre or not nombre.startswith(PREFIJO_AVATAR):
        return None
    numero = nombre[len(PREFIJO_AVATAR):].split(".", 1)[0]
    return int(numero) if numero.isdigit() else None


def _cargar_avatares() -> None:
    """Lista el bucket de avatares una sola vez y arma el mapa id -> archivo."""
    global _avatares_cargado
    if _avatares_cargado:
        return
    try:
        items = _bucket(BUCKET_AVATARES).list("", {"limit": 100}) or []
    except Exception:  # noqa: BLE001
        log.debug("No se pudo listar el bucket %s", BUCKET_AVATARES, exc_info=True)
        items = []
    for item in items:
        nombre = item.get("name") or ""
        uid = _id_desde_avatar(nombre)
        if uid is not None:
            _avatares[uid] = nombre
    _avatares_cargado = True


def obtener_avatar_url(usuario_id) -> str | None:
    """URL pública de la foto de perfil del usuario, o None si no tiene."""
    if not usuario_id:
        return None
    try:
        uid = int(usuario_id)
    except (TypeError, ValueError):
        return None
    _cargar_avatares()
    nombre = _avatares.get(uid)
    return url_publica(BUCKET_AVATARES, nombre) if nombre else None


def subir_avatar(archivo, usuario_id) -> str:
    """Sube (o reemplaza) la foto de perfil del usuario y devuelve su URL.

    El nombre es estable (`usuario-<id>.<ext>`); si cambia la extensión se
    elimina la versión anterior para no dejar archivos huérfanos.
    """
    try:
        uid = int(usuario_id)
    except (TypeError, ValueError):
        raise ValidationError("No se pudo identificar al usuario.") from None

    nombre_original = (getattr(archivo, "filename", "") or "").strip().lower()
    ext = nombre_original.rsplit(".", 1)[-1] if "." in nombre_original else ""
    if ext == "svg":
        raise ValidationError("Para tu foto de perfil usa PNG, JPG o WEBP.")

    url, ruta = subir_imagen(
        BUCKET_AVATARES, f"{PREFIJO_AVATAR}{uid}", archivo, upsert=True
    )

    # Limpia versiones previas con otra extensión (usuario-5.png vs .jpg).
    _cargar_avatares()
    anterior = _avatares.get(uid)
    if anterior and anterior != ruta:
        try:
            _bucket(BUCKET_AVATARES).remove([anterior])
        except Exception:  # noqa: BLE001
            pass
    _avatares[uid] = ruta
    return url


def quitar_avatar(usuario_id) -> None:
    """Elimina la foto de perfil del usuario (vuelve al avatar de iniciales)."""
    try:
        uid = int(usuario_id)
    except (TypeError, ValueError):
        return
    _cargar_avatares()
    nombre = _avatares.pop(uid, None)
    if nombre:
        try:
            _bucket(BUCKET_AVATARES).remove([nombre])
        except Exception:  # noqa: BLE001
            pass
