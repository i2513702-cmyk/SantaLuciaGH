"""Fábrica de la aplicación (patrón Application Factory)."""

import os

from flask import Flask, render_template, url_for

from app.config import config_map
from app.decorators import bloquear_compras_sin_rol
from app.extensions import bcrypt, cors


def create_app() -> Flask:
    env = os.getenv("FLASK_ENV", "development")
    app = Flask(__name__)
    app.config.from_object(config_map.get(env, config_map["development"]))

    if app.config.get("TRUST_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # --- Inicializar extensiones ---
    cors.init_app(app)
    bcrypt.init_app(app)

    # --- Blueprints ---
    from app.routes import admin, api, auth, carrito, checkout, citas, kpi, main, seguridad, ventas, worker
    from app.routes.supabase import bp as supabase_bp

    app.register_blueprint(main.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(worker.bp)
    app.register_blueprint(carrito.bp)
    app.register_blueprint(checkout.bp)
    app.register_blueprint(citas.bp)
    app.register_blueprint(kpi.bp)
    app.register_blueprint(seguridad.bp)
    app.register_blueprint(ventas.bp)
    app.register_blueprint(supabase_bp)
    app.register_blueprint(api.bp)

    # --- Comandos CLI ---
    from app.cli import register_cli

    register_cli(app)

    # --- Calentar conexión a Supabase en segundo plano (primer login rápido) ---
    from app.supabase_client import calentar

    calentar()

    # --- Limpiar carritos vencidos en segundo plano al arrancar ---
    from app.services.carrito_service import iniciar_purga

    iniciar_purga()

    # --- Iconos: imagen propia si existe en static/img/icons, si no Bootstrap Icons ---
    from markupsafe import Markup, escape

    from app.config import Config as _Cfg

    _cache_iconos: dict = {}

    def _archivo_icono(nombre):
        if nombre not in _cache_iconos or app.debug:
            carpeta = os.path.join(app.static_folder, *_cfg_dir.split("/"))
            _cache_iconos[nombre] = next(
                (f"{_cfg_dir}/{nombre}{e}" for e in (".svg", ".webp", ".png", ".jpg")
                 if os.path.isfile(os.path.join(carpeta, nombre + e))),
                None,
            )
        return _cache_iconos[nombre]

    _cfg_dir = _Cfg.ICONS_DIR

    @app.template_global()
    def icono(nombre, bi=None, size=24, clase=""):
        """<img> si hay archivo static/img/icons/<nombre>.*, si no <i class='bi bi-<bi>'>."""
        ruta = _archivo_icono(nombre)
        if ruta:
            return Markup(
                f'<img src="{escape(url_for("static", filename=ruta))}" alt="" width="{int(size)}" '
                f'height="{int(size)}" class="icono-img {escape(clase)}">'
            )
        return Markup(f'<i class="bi bi-{escape(bi or nombre)} {escape(clase)}" style="font-size:{int(size)}px"></i>')

    # --- Contexto global para las plantillas ---
    @app.context_processor
    def inject_globals():
        from flask import session

        from app.decorators import ROLES_VENTAS, es_administrativo, map_rol, puede_comprar
        from app.security import get_csrf

        rol = session.get("rol")
        # El contador del carrito solo existe para quien puede comprar; así el
        # personal interno nunca ve un carrito propio en la cabecera.
        carrito_n = session.get("carrito_n", 0) if puede_comprar(rol) else 0

        return {
            "app_name": app.config["APP_NAME"],
            "app_tagline": app.config["APP_TAGLINE"],
            "logo_path": app.config["LOGO_PATH"],
            "footer_bg": app.config["FOOTER_BG_PATH"],
            "rol": rol,
            "rol_panel": map_rol(rol),
            "es_admin": es_administrativo(rol),
            "puede_comprar": puede_comprar(rol),
            "puede_ventas": rol in ROLES_VENTAS,
            "nombre": session.get("nombre"),
            "carrito_n": carrito_n,
            "csrf_token": get_csrf(),
        }

    # --- El personal interno no compra: se corta el carrito/checkout antes del CSRF ---
    app.before_request(bloquear_compras_sin_rol)

    # --- Protección CSRF en todas las peticiones POST ---
    @app.before_request
    def proteger_csrf():
        from flask import flash, redirect, request, url_for

        from app.security import csrf_valido

        if request.method != "POST":
            return None

        token = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
        if csrf_valido(token):
            return None

        flash("Tu sesión expiró o el formulario no es válido. Vuelve a intentarlo.", "error")
        destino = request.referrer
        if not destino or not destino.startswith(request.host_url):
            destino = url_for("main.home")
        return redirect(destino)

    # --- Manejadores de errores ---
    @app.errorhandler(403)
    def forbidden(_):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_):
        return render_template("errors/404.html"), 404

    return app