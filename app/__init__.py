"""Fábrica de la aplicación (patrón Application Factory)."""

import os

from flask import Flask, render_template

from app.config import config_map
from app.extensions import bcrypt, cors


def create_app() -> Flask:
    env = os.getenv("FLASK_ENV", "development")
    app = Flask(__name__)
    app.config.from_object(config_map.get(env, config_map["development"]))

    # --- Inicializar extensiones ---
    cors.init_app(app)
    bcrypt.init_app(app)

    # --- Blueprints ---
    from app.routes import admin, auth, carrito, main, worker
    from app.routes.supabase import bp as supabase_bp

    app.register_blueprint(main.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(worker.bp)
    app.register_blueprint(carrito.bp)
    app.register_blueprint(supabase_bp)

    # --- Comandos CLI ---
    from app.cli import register_cli

    register_cli(app)

    # --- Calentar conexión a Supabase en segundo plano (primer login rápido) ---
    from app.supabase_client import calentar

    calentar()

    # --- Limpiar carritos vencidos en segundo plano al arrancar ---
    from app.services.carrito_service import iniciar_purga

    iniciar_purga()

    # --- Contexto global para las plantillas ---
    @app.context_processor
    def inject_globals():
        from flask import session

        from app.decorators import map_rol

        return {
            "app_name": app.config["APP_NAME"],
            "app_tagline": app.config["APP_TAGLINE"],
            "logo_path": app.config["LOGO_PATH"],
            "footer_bg": app.config["FOOTER_BG_PATH"],
            "rol": session.get("rol"),
            "rol_panel": map_rol(session.get("rol")),
            "nombre": session.get("nombre"),
            "carrito_n": session.get("carrito_n", 0),
        }

    # --- Manejadores de errores ---
    @app.errorhandler(403)
    def forbidden(_):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_):
        return render_template("errors/404.html"), 404

    return app