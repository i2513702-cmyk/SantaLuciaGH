"""Decoradores para controlar acceso por roles (sesión real contra la BD)."""

from functools import wraps

from flask import abort, redirect, request, session, url_for

ROLES_DB = {
    "ADMINISTRADOR": "Administrador",
    "SUPERVISOR": "Supervisor",
    "TECNICO": "Técnico",
    "RECEPCIONISTA": "Recepcionista",
    "VENDEDOR": "Vendedor",
    "ALMACENERO": "Almacenero",
    "CLIENTE": "Cliente",
}

# Blueprints de compra (carrito y checkout): bloqueados para quien no compra.
BLUEPRINTS_COMPRA = ("carrito", "checkout")

# El panel administrativo es exclusivo del administrador: el rol que dirige el
# sistema. ADMIN_ROLES suma el supervisor, que además accede a la gestión de
# cuentas. Ojo: esto NO decide quién compra, solo qué menú se muestra.
ROLES_PANEL_ADMIN = ("ADMINISTRADOR",)
ADMIN_ROLES = ("ADMINISTRADOR", "SUPERVISOR")

# Roles que NO operan como compradores en la tienda. El personal interno
# (administración, supervisión, taller, almacén y mostrador) trabaja con la
# operación; la venta la registra quien atiende al cliente, no el técnico ni el
# supervisor. El único rol que compra es CLIENTE, y las visitas anónimas
# también (rol ausente -> permitido).
#
# OJO al añadir un rol nuevo a ROLES_DB: esta lista es una denegación, así que
# un rol nuevo compraría por defecto hasta que se agregue aquí.
ROLES_SIN_COMPRA = (
    "ADMINISTRADOR",
    "SUPERVISOR",
    "TECNICO",
    "RECEPCIONISTA",
    "VENDEDOR",
    "ALMACENERO",
)

# Roles que ven el modulo de ventas/ordenes.
ROLES_VENTAS = ("ADMINISTRADOR", "SUPERVISOR", "VENDEDOR", "RECEPCIONISTA")

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


def es_administrativo(rol) -> bool:
    """True si el rol dirige el panel administrativo (menú de gestión)."""
    return rol in ROLES_PANEL_ADMIN


def es_no_comprador(rol) -> bool:
    """True si el rol no puede comprar en la tienda (personal interno)."""
    return rol in ROLES_SIN_COMPRA


def puede_comprar(rol) -> bool:
    """True si el rol opera como comprador en la tienda.

    Solo el cliente compra; el personal interno gestiona, repara o atiende, y
    por eso no ve el carrito ni puede pasar por el checkout. Las visitas
    anónimas (sin rol en sesión) también pueden comprar.
    """
    return not es_no_comprador(rol)


def panel_for(rol):
    """Devuelve el endpoint del panel según el rol de la BD."""
    return {
        "ADMINISTRADOR": "admin.dashboard",
        "SUPERVISOR": "worker.dashboard",
        "TECNICO": "worker.dashboard",
        "RECEPCIONISTA": "worker.dashboard",
        "VENDEDOR": "worker.dashboard",
        "ALMACENERO": "worker.dashboard",
        "CLIENTE": "main.home",
    }.get(rol, "main.home")


def _denegar():
    """Corta la petición con la página 403 del proyecto."""
    abort(403)


def login_required(view):
    """Requiere iniciar sesión. Si no, redirige al login (y vuelve al destino)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    """Requiere rol de administración (ADMINISTRADOR/SUPERVISOR)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("auth.login"))
        if session.get("rol") not in ADMIN_ROLES:
            _denegar()
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
                _denegar()
            return view(*args, **kwargs)

        return wrapped

    return decorator


def bloquear_compras_sin_rol():
    """Corta con 403 el carrito y el checkout de quien no puede comprar.

    Se registra como before_request global antes de la validación CSRF, para
    que ni siquiera un POST suyo llegue a procesar una compra.
    """
    if request.blueprint in BLUEPRINTS_COMPRA and es_no_comprador(session.get("rol")):
        _denegar()


def ventas_required(view):
    """Acceso al modulo de ventas: administrador, supervisor, vendedor y recepcionista."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("auth.login", next=request.path))
        if session.get("rol") not in ROLES_VENTAS:
            _denegar()
        return view(*args, **kwargs)

    return wrapped
