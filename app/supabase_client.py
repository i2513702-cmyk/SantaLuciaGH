"""Cliente de Supabase (equivale al db.py del proyecto de referencia).

Conecta a Supabase usando las variables de entorno configuradas en .env:
  - SUPABASE_URL           -> https://<ref>.supabase.co
  - SUPABASE_KEY           -> clave publishable/anon (ej. sb_publishable_...)
  - SUPABASE_SERVICE_KEY   -> (opcional) service_role para escrituras servidor

Expone:
  - get_supabase()      -> cliente leyendo con SUPABASE_KEY
  - get_admin_client()  -> cliente de ESCRITURA con SUPABASE_SERVICE_KEY
  - test_connection()   -> verifica URL, key y health de Supabase Auth
"""

import threading

from supabase import Client, ClientOptions, create_client

from app.config import Config

_client = None
_admin_client = None
_TIMEOUT = 10


def is_configured(service: bool = False) -> bool:
    """Devuelve True si hay URL y key configuradas (y no placeholders)."""
    url = Config.SUPABASE_URL or ""
    key = Config.SUPABASE_SERVICE_KEY if service else Config.SUPABASE_KEY
    key = key or ""
    return bool(url and key and "pon_aqui" not in key)


def _crear(url, key):
    return create_client(
        url, key, options=ClientOptions(postgrest_client_timeout=_TIMEOUT)
    )


def get_supabase() -> Client:
    """Cliente de lectura con SUPABASE_KEY (publishable/anon)."""
    global _client
    if _client is None:
        if not is_configured():
            raise RuntimeError(
                "Supabase no está configurado. Revisa SUPABASE_URL y "
                "SUPABASE_KEY en el archivo .env"
            )
        _client = _crear(Config.SUPABASE_URL, Config.SUPABASE_KEY)
    return _client


def get_reader() -> Client:
    """Cliente para LEER datos del servidor.

    Usa SUPABASE_SERVICE_KEY si está configurada (omite RLS y ve todas las
    filas). Si no, cae en la publishable key (puede no ver filas si RLS
    está activa).
    """
    if is_configured(service=True):
        return get_admin_client()
    return get_supabase()


def get_admin_client() -> Client:
    """Cliente de escritura con SUPABASE_SERVICE_ROLE (omite RLS).

    Necesario para INSERT/UPDATE/DELETE porque la publishable key solo lee
    (RLS activa en el proyecto). NUNCA debe exponerse al frontend.
    """
    global _admin_client
    if _admin_client is None:
        if not is_configured(service=True):
            raise RuntimeError(
                "Falta SUPABASE_SERVICE_KEY en el .env. Cópiala desde "
                "Supabase -> Settings -> API -> service_role (secreto). "
                "Se usa solo en el servidor para escrituras."
            )
        _admin_client = _crear(Config.SUPABASE_URL, Config.SUPABASE_SERVICE_KEY)
    return _admin_client


def calentar() -> None:
    """Hace una consulta rápida en segundo plano al arrancar.

    La primera conexión a Supabase es la lenta (establece DNS + TLS). Al calentar
    en un hilo daemon, el primer login del usuario no paga esa latencia.
    """
    def _tarea():
        try:
            get_reader().table("tipo_documento").select("id").limit(1).execute()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_tarea, daemon=True).start()


def error_postgrest(exc: Exception) -> str:
    """Convierte un error de PostgREST/supabase-py en un mensaje legible."""
    codigo = getattr(exc, "code", None)
    mensaje = getattr(exc, "message", None) or str(exc)
    if codigo == "PGRST205":
        return (
            "La tabla aún no existe en Supabase. "
            "Crea las tablas del proyecto y vuelve a intentar."
        )
    if codigo == "42501":
        return (
            "Permiso denegado (RLS): la publishable key solo lee. "
            "Configura SUPABASE_SERVICE_KEY en el .env para escrituras."
        )
    if codigo:
        return f"Error de Supabase ({codigo}): {mensaje}"
    return mensaje


def test_connection() -> dict:
    """Comprueba la conexión real contra Supabase y devuelve un reporte.

    Prueba de `auth/v1/health` (disponibilidad) y una consulta PostgREST a
    una tabla inexistente para validar que la API key autentica.
    """
    if not is_configured():
        return {
            "ok": False,
            "configurado": False,
            "detalle": "Faltan SUPABASE_URL o SUPABASE_KEY en el .env",
        }

    import urllib.error
    import urllib.request

    base = Config.SUPABASE_URL.rstrip("/")

    def _status(path, metodo="GET"):
        """Devuelve el código HTTP de una petición (manejando errores HTTP)."""
        headers = {"apikey": Config.SUPABASE_KEY, "Authorization": f"Bearer {Config.SUPABASE_KEY}"}
        req = urllib.request.Request(base + path, headers=headers, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception:  # noqa: BLE001
            return None

    reachable = _status("/auth/v1/health") == 200

    key_valida = False
    # 404 = la tabla no existe pero la API key autenticó correctamente.
    key_valida = _status("/rest/v1/tabla_inexistente_check") in (404, 200)

    return {
        "ok": reachable and key_valida,
        "configurado": True,
        "reachable": reachable,
        "key_valida": key_valida,
        "url": base,
        "detalle": "Supabase accesible y la API key autentica correctamente."
        if reachable and key_valida
        else "Revisa que SUPABASE_URL y SUPABASE_KEY sean correctos.",
    }