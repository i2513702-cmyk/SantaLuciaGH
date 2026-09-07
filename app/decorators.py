"""Decoradores para controlar acceso por roles (sesión real contra la BD)."""

from functools import wraps

from flask import redirect, session, url_for

ROLES_DB = {
    "ADMINISTRADOR": "Administrador",
    "SUPERVISOR": "Supervisor",
    "TECNICO": "Técnico",
    "RECEPCIONISTA": "Recepcionista",
    "VENDEDOR": "Vendedor",
    "ALMACENERO": "Almacenero",
}

ADMIN_ROLES = ("ADMINISTRADOR", "SUPERVISOR")

_ROLES_PANEL = {
    "ADMINISTRADOR": "admin",
    "SUPERVISOR": "worker",
    "TECNICO": "worker",
    "RECEPCIONISTA": "worker",
    "VENDEDOR": "worker",
    "ALMACENERO": "worker",
}


def map_rol(rol):
    """Traduce un rol de la base de datos al rol de la interfaz (admin/worker)."""
    return _ROLES_PANEL.get(rol)


def panel_for(rol):
    """Devuelve el endpoint del panel según el rol de la BD."""
    return {
        "ADMINISTRADOR": "admin.dashboard",
        "SUPERVISOR": "worker.dashboard",
        "TECNICO": "worker.dashboard",
        "RECEPCIONISTA": "worker.dashboard",
        "VENDEDOR": "worker.dashboard",
        "ALMACENERO": "worker.dashboard",
    }.get(rol, "main.home")


def login_required(view):
    """Requiere iniciar sesión. Si no, redirige al login."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    """Requiere rol de administración (ADMINISTRADOR/SUPERVISOR)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("auth.login"))
        if session.get("rol") not in ADMIN_ROLES:
            return "Acceso denegado", 403
        return view(*args, **kwargs)

    return wrapped


def roles_required(*roles_panel):
    """Permite el acceso solo a los roles de interfaz indicados ('admin'/'worker'/...)."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "usuario_id" not in session:
                return redirect(url_for("auth.login"))
            if map_rol(session.get("rol")) not in roles_panel:
                return "Acceso denegado", 403
            return view(*args, **kwargs)

        return wrapped

    return decorator