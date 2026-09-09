"""Configuración de la aplicación.

Los valores sensibles se leen desde el archivo .env (ver .env.example).
"""

import os

from dotenv import load_dotenv

load_dotenv()


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

    # Límite de subida de archivos (2 MB)
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    UPLOAD_FOLDER = "app/static/img/uploads"

    # Vigencia de los carritos en la base de datos (en horas).
    # Pasado este tiempo desde la última creación, el carrito se invalida solo.
    CART_TTL_HOURS = 3


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}
