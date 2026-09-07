"""Panel de administración (datos reales vía API REST de Supabase)."""

from flask import Blueprint, render_template

from app.decorators import roles_required
from app.supabase_client import get_reader

bp = Blueprint("admin", __name__)

TABLAS_CONTEO = ("clientes", "productos","Tecnicos", "reparaciones", "usuarios")


def _contar(tabla: str):
    """Devuelve el total de filas de una tabla; None si la consulta falla."""
    try:
        resp = get_reader().table(tabla).select("*", count="exact").limit(1).execute()
        return resp.count
    except Exception:  # noqa: BLE001
        return None


@bp.route("/admin")
@roles_required("admin")
def dashboard():
    conteos = {t: _contar(t) for t in TABLAS_CONTEO}

    stats = [
        {"label": "Técnicos", "value": conteos.get("tecnicos") or 0, "icon": "🔧"},
        {"label": "Clientes", "value": conteos.get("clientes") or 0, "icon": "👥"},
        {"label": "Productos", "value": conteos.get("productos") or 0, "icon": "🏷️"},
        {"label": "Reparaciones", "value": conteos.get("reparaciones") or 0, "icon": "🛠️"},
        {"label": "Usuarios", "value": conteos.get("usuarios") or 0, "icon": "🔐"},
    ]
    modulos = [
        {
            "nombre": "Usuarios",
            "descripcion": "Administración de cuentas del sistema",
            "icon": "🔐",
            "url": "auth.usuarios_sistema",
        },
        {
            "nombre": "Cambiar contraseña",
            "descripcion": "Actualiza tu clave de acceso",
            "icon": "🔑",
            "url": "auth.cambiar_clave",
        },
        {
            "nombre": "Estado de Supabase",
            "descripcion": "Verifica la conexión con la base de datos",
            "icon": "🦾",
            "url": "supabase.status",
        },
    ]
    return render_template("admin/dashboard.html", stats=stats, modulos=modulos)