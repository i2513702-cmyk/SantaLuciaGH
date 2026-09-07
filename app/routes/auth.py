"""Autenticación (login real contra Supabase, logout, cambio de clave y gestión de usuarios).

Inspirado en el app.py del proyecto de referencia: sesión + bcrypt.
"""

from datetime import datetime, timezone

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.decorators import admin_required, login_required, panel_for
from app.exceptions import AppError, ValidationError
from app.services import rest_auth_service

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if "usuario_id" in session:
        return redirect(url_for("auth.panel"))

    error = None
    if request.method == "POST":
        identificador = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        try:
            usuario = rest_auth_service.login(identificador, password)
            session.clear()
            session["usuario_id"] = usuario["id"]
            session["username"] = usuario["nombre_usuario"]
            session["nombre"] = usuario["nombre_usuario"]
            session["rol"] = usuario["rol"]
            import threading

            threading.Thread(
                target=rest_auth_service.actualizar_ultimo_acceso,
                args=(usuario["nombre_usuario"], datetime.now(timezone.utc).isoformat()),
                daemon=True,
            ).start()
            return redirect(url_for("auth.panel"))
        except (AppError, ValidationError) as exc:
            error = exc.message if isinstance(exc, AppError) else str(exc)
        except Exception:  # noqa: BLE001
            error = "No se pudo conectar con Supabase. Revisa la configuración."

    return render_template("auth/login.html", error=error)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/panel")
@login_required
def panel():
    """Redirige a cada usuario a su panel según su rol."""
    return redirect(url_for(panel_for(session.get("rol"))))


@bp.route("/registrarse", methods=["GET", "POST"])
def registrarse():
    """Crea una cuenta nueva (empleado + usuario) directamente en Supabase."""
    if "usuario_id" in session:
        return redirect(url_for("auth.panel"))

    error = None
    if request.method == "POST":
        try:
            usuario = rest_auth_service.register(
                nombre_usuario=request.form.get("nombre_usuario", "").strip(),
                correo=request.form.get("correo", "").strip().lower(),
                password=request.form.get("password", ""),
                rol=request.form.get("rol", "RECEPCIONISTA").strip(),
                nombres=request.form.get("nombres", "").strip(),
                apellidos=request.form.get("apellidos", "").strip(),
                tipo_documento_id=int(request.form.get("tipo_documento_id") or 1),
                numero_documento=request.form.get("numero_documento", "").strip(),
                telefono=request.form.get("telefono", "").strip(),
                cargo=request.form.get("cargo", "Empleado").strip() or "Empleado",
            )
            session.clear()
            session["usuario_id"] = usuario["id"]
            session["username"] = usuario["nombre_usuario"]
            session["nombre"] = usuario["nombre_usuario"]
            session["rol"] = usuario["rol"]
            flash("Cuenta creada correctamente. ¡Bienvenido!", "success")
            return redirect(url_for("auth.panel"))
        except (AppError, ValidationError) as exc:
            error = exc.message if isinstance(exc, AppError) else str(exc)
        except Exception:  # noqa: BLE001
            error = "No se pudo crear la cuenta. Revisa los datos e inténtalo de nuevo."

    tipos_documento = None
    try:
        tipos_documento = rest_auth_service.listar_tipos_documento()
    except Exception:  # noqa: BLE001
        tipos_documento = [{"id": 1, "codigo": "DNI", "nombre": "DNI"}]
    return render_template(
        "auth/registrarse.html",
        error=error,
        tipos_documento=tipos_documento,
    )


@bp.route("/cambiar_clave", methods=["GET", "POST"])
@login_required
def cambiar_clave():
    if request.method == "POST":
        nueva = request.form.get("nueva", "")
        try:
            rest_auth_service.cambiar_clave(session["username"], nueva)
            flash("Contraseña actualizada correctamente.", "success")
            return redirect(url_for("auth.panel"))
        except (AppError, ValidationError) as exc:
            error = exc.message if isinstance(exc, AppError) else str(exc)
            return render_template("auth/cambiar_clave.html", error=error)
    return render_template("auth/cambiar_clave.html")


@bp.route("/sistema/usuarios")
@login_required
@admin_required
def usuarios_sistema():
    """Lista los usuarios del sistema (vía Supabase REST)."""
    try:
        usuarios = rest_auth_service.listar_usuarios()
    except ValidationError as exc:
        flash(str(exc), "error")
        usuarios = []
    return render_template("auth/usuarios_sistema.html", usuarios=usuarios)


@bp.route("/sistema/usuarios/nuevo", methods=["GET", "POST"])
@login_required
@admin_required
def usuarios_sistema_nuevo():
    """Crea un usuario con la contraseña por defecto '123456' (bcrypt), como en la referencia."""
    if request.method == "POST":
        try:
            rest_auth_service.crear_usuario(
                nombre_usuario=request.form.get("nombre_usuario", "").strip(),
                correo=request.form.get("correo", "").strip(),
                password=request.form.get("password", "123456"),
                rol=request.form.get("rol", "RECEPCIONISTA").strip(),
                empleado_id=int(request.form.get("empleado_id") or 0),
            )
            flash("Usuario creado correctamente (contraseña por defecto: 123456).", "success")
            return redirect(url_for("auth.usuarios_sistema"))
        except (AppError, ValidationError) as exc:
            error = exc.message if isinstance(exc, AppError) else str(exc)
            return render_template("auth/usuarios_sistema_form.html", error=error)
    return render_template("auth/usuarios_sistema_form.html")