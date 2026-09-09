"""Panel de administración (datos reales vía API REST de Supabase)."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.decorators import roles_required
from app.exceptions import AppError, NotFoundError, ValidationError
from app.services import producto_service
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
            "nombre": "Productos",
            "descripcion": "Agregar, editar y eliminar productos del catálogo",
            "icon": "🏷️",
            "url": "admin.productos",
        },
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


def _form_ctx(producto=None, valores=None):
    """Devuelve el contexto común del formulario de producto."""
    try:
        marcas = producto_service.listar_marcas()
        categorias = producto_service.listar_categorias()
    except ValidationError:
        marcas, categorias = [], []
    datos = dict(producto or {}) if producto else (dict(valores) if valores else {})
    if datos:
        for campo in ("marca_id", "categoria_id"):
            try:
                datos[campo] = int(datos.get(campo) or 0)
            except (TypeError, ValueError):
                datos[campo] = 0
        datos["activo"] = datos.get("activo") in (True, "on")
    return {"producto": datos, "marcas": marcas, "categorias": categorias}


@bp.route("/admin/productos")
@roles_required("admin")
def productos():
    try:
        lista = producto_service.listar_productos()
    except ValidationError as exc:
        flash(str(exc), "error")
        lista = []
    return render_template("admin/productos.html", productos=lista)


@bp.route("/admin/productos/nuevo", methods=["GET", "POST"])
@roles_required("admin")
def producto_nuevo():
    error = None
    valores = None
    if request.method == "POST":
        try:
            producto_service.crear_producto(request.form)
            flash("Producto creado correctamente.", "success")
            return redirect(url_for("admin.productos"))
        except (AppError, ValidationError) as exc:
            error = exc.message if isinstance(exc, AppError) else str(exc)
        except Exception:  # noqa: BLE001
            error = "No se pudo crear el producto. Revisa los datos e inténtalo de nuevo."
        valores = request.form
    ctx = _form_ctx(producto=valores)
    ctx["error"] = error
    return render_template("admin/producto_form.html", **ctx)


@bp.route("/admin/productos/<int:producto_id>/editar", methods=["GET", "POST"])
@roles_required("admin")
def producto_editar(producto_id):
    error = None
    valores = None
    if request.method == "POST":
        try:
            producto_service.actualizar_producto(producto_id, request.form)
            flash("Producto actualizado correctamente.", "success")
            return redirect(url_for("admin.productos"))
        except (AppError, ValidationError) as exc:
            error = exc.message if isinstance(exc, AppError) else str(exc)
        except Exception:  # noqa: BLE001
            error = "No se pudo actualizar el producto. Revisa los datos e inténtalo de nuevo."
        valores = request.form
    else:
        try:
            valores = producto_service.obtener_producto(producto_id)
        except (AppError, ValidationError) as exc:
            flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
            return redirect(url_for("admin.productos"))

    ctx = _form_ctx(producto=valores)
    ctx["error"] = error
    return render_template("admin/producto_form.html", **ctx)


@bp.post("/admin/productos/<int:producto_id>/eliminar")
@roles_required("admin")
def producto_eliminar(producto_id):
    try:
        producto_service.eliminar_producto(producto_id)
        flash("Producto eliminado correctamente.", "success")
    except (AppError, ValidationError) as exc:
        flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
    except Exception:  # noqa: BLE001
        flash("No se pudo eliminar el producto.", "error")
    return redirect(url_for("admin.productos"))