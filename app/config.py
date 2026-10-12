"""Configuración de la aplicación.

Los valores sensibles se leen desde el archivo .env (ver .env.example).
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _float_env(nombre: str, defecto: float) -> float:
    """Lee un float del .env sin romper la app si el valor esta mal escrito."""
    try:
        return float(str(os.getenv(nombre, defecto)).strip())
    except (TypeError, ValueError):
        return defecto


class Config:
    """Configuración base compartida por todos los entornos."""

    SECRET_KEY = os.getenv("SECRET_KEY", "clave-insegura-para-desarrollo")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Marcas y nombre comercial
    APP_NAME = "Santa Lucia"
    APP_TAGLINE = "Todo para tu hogar y más"

    # Supabase (API REST, auth, storage y notificaciones para módulos futuros)
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    SUPABASE_WEBHOOK_SECRET = os.getenv("SUPABASE_WEBHOOK_SECRET", "")

    # Imágenes (se usará al agregar catálogo de productos/servicios)
    LOGO_PATH = "img/logo.svg"
    DEFAULT_PRODUCT_IMAGE = "img/producto-default.svg"
    FOOTER_BG_PATH = "img/fondo-footer.svg"

    # Seguridad del login (por IP): a los N fallos hay espera; pasada la espera,
    # M fallos mas bloquean la IP hasta que un administrador la desbloquee.
    LOGIN_MAX_INTENTOS = int(_float_env("LOGIN_MAX_INTENTOS", 3))
    LOGIN_ESPERA_MIN = int(_float_env("LOGIN_ESPERA_MIN", 5))
    LOGIN_FALLOS_BLOQUEO = int(_float_env("LOGIN_FALLOS_BLOQUEO", 2))
    TRUST_PROXY = os.getenv("TRUST_PROXY", "0") == "1"

    # Iconos: si existe static/img/icons/<nombre>.(svg|png|webp|jpg) se usa la imagen;
    # si no, el icono de Bootstrap Icons.
    ICONS_DIR = "img/icons"

    # Límite de subida de archivos (2 MB)
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    UPLOAD_FOLDER = "app/static/img/uploads"

    # Vigencia de los carritos en la base de datos (en horas).
    # Pasado este tiempo desde la última creación, el carrito se invalida solo.
    CART_TTL_HOURS = 3

    # ---- Metas de los KPIs del panel administrativo -----------------------
    # Configurables por .env (ver .env.example): no hay tabla de metas ni
    # migraciones, los umbrales viven en la configuracion de la app.
    # KPI-01 se expresa en horas (menor es mejor); los demas en porcentaje.
    KPI_META_01_HORAS = _float_env("KPI_META_01_HORAS", 24)
    KPI_META_02_PCT = _float_env("KPI_META_02_PCT", 90)
    KPI_META_03_PCT = _float_env("KPI_META_03_PCT", 2)
    KPI_META_04_PCT = _float_env("KPI_META_04_PCT", 85)
    KPI_META_05_PCT = _float_env("KPI_META_05_PCT", 70)
    KPI_META_06_PCT = _float_env("KPI_META_06_PCT", 95)

    # Margen (porcentaje) para el estado "cerca de la meta" (semaforo ambar).
    KPI_TOLERANCIA_PCT = _float_env("KPI_TOLERANCIA_PCT", 10)

    # Historico semanal de KPI-04 (disponibilidad). Como no se crean tablas
    # nuevas, cada snapshot se agrega a este archivo JSONL.
    KPI_SNAPSHOT_FILE = os.getenv("KPI_SNAPSHOT_FILE", "data/kpi_snapshots.jsonl")


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}
