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

from app.decorators import admin_required, es_no_comprador, login_required, panel_for
from app.exceptions import AppError, AuthorizationError, ValidationError
from app.services import login_guard, rest_auth_service

bp = Blueprint("auth", __name__)


def _sincronizar_carrito():
    """Reclama el carrito anónimo (si existe) y refresca el contador al usuario.

    El personal interno no compra: se descarta cualquier carrito previo
    y no se reclama nada, para que su sesión quede limpia.
    """
    session.pop("carrito_id", None)

    if es_no_comprador(session.get("rol")):
        session["carrito_n"] = 0
        return

    from app.services import carrito_service

    token = session.get("carrito_sesion")
    carrito_id = session.get("carrito_id")
    user_key = f"user:{session['usuario_id']}"
    if carrito_id and token:
        carrito_service.reclamar(carrito_id, token, user_key)
    session["carrito_n"] = carrito_service.contar_por_usuario(user_key)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if "usuario_id" in session:
        return redirect(url_for("auth.panel"))

    ip = request.remote_addr or "desconocida"
    error = None
    bloqueo = login_guard.estado(ip)

    if request.method == "POST":
        error = login_guard.mensaje_bloqueo(bloqueo)
        if not error:
            identificador = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            try:
                usuario = rest_auth_service.login(identificador, password)
                login_guard.registrar_exito(ip)
                session.clear()
                session["usuario_id"] = usuario["id"]
                session["username"] = usuario["nombre_usuario"]
                session["nombre"] = usuario["nombre_usuario"]
                session["rol"] = usuario["rol"]
                _sincronizar_carrito()
                import threading

                threading.Thread(
                    target=rest_auth_service.actualizar_ultimo_acceso,
                    args=(usuario["nombre_usuario"], datetime.now(timezone.utc).isoformat()),
                    daemon=True,
                ).start()
                destino = (request.args.get("next") or "").strip()
                if not destino or not destino.startswith("/") or destino.startswith("//"):
                    destino = url_for("auth.panel")
                return redirect(destino)
            except AuthorizationError as exc:
                error = login_guard.registrar_fallo(ip, identificador) or exc.message
                bloqueo = login_guard.estado(ip)
            except (AppError, ValidationError) as exc:
                error = exc.message if isinstance(exc, AppError) else str(exc)
            except Exception:  # noqa: BLE001
                error = "No se pudo conectar con Supabase. Revisa la configuración."

    return render_template("auth/login.html", error=error, bloqueo=bloqueo)


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
                rol="CLIENTE",
                nombres=request.form.get("nombres", "").strip(),
                apellidos=request.form.get("apellidos", "").strip(),
                tipo_documento_id=int(request.form.get("tipo_documento_id") or 1),
                numero_documento=request.form.get("numero_documento", "").strip(),
                telefono=request.form.get("telefono", "").strip(),
            )
            session.clear()
            session["usuario_id"] = usuario["id"]
            session["username"] = usuario["nombre_usuario"]
            session["nombre"] = usuario["nombre_usuario"]
            session["rol"] = usuario["rol"]
            _sincronizar_carrito()
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
    """Cambio de contraseña: verifica la actual, valida confirmación y
    guarda el nuevo hash bcrypt en la BD. Vive embebido en el perfil."""
    if request.method == "POST":
        actual = request.form.get("actual", "")
        nueva = request.form.get("nueva", "")
        confirmar = request.form.get("confirmar", "")
        if nueva != confirmar:
            flash("La confirmación no coincide con la nueva contraseña.", "error")
            return redirect(url_for("main.perfil"))
        try:
            rest_auth_service.cambiar_clave(session["username"], actual, nueva)
            flash("Contraseña actualizada correctamente.", "success")
        except (AppError, ValidationError) as exc:
            flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
        except Exception:  # noqa: BLE001
            flash("No se pudo actualizar la contraseña. Inténtalo de nuevo.", "error")
        return redirect(url_for("main.perfil"))
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


@bp.route("/sistema/usuarios/<int:usuario_id>/editar", methods=["GET", "POST"])
@login_required
@admin_required
def usuario_editar(usuario_id):
    """Edita los datos de un usuario: nombre, correo, rol, empleado y estado."""
    error = None
    valores = None

    if request.method == "POST":
        activo = request.form.get("activo") == "on"
        if usuario_id == session.get("usuario_id") and not activo:
            error = "No puedes desactivar tu propia cuenta."
        else:
            try:
                rest_auth_service.actualizar_usuario(
                    usuario_id=usuario_id,
                    nombre_usuario=request.form.get("nombre_usuario", "").strip(),
                    correo=request.form.get("correo", "").strip(),
                    rol=request.form.get("rol", "").strip(),
                    activo=activo,
                    empleado_id=request.form.get("empleado_id", 0),
                    cliente_id=request.form.get("cliente_id", 0),
                )
                flash("Usuario actualizado correctamente.", "success")
                return redirect(url_for("auth.usuarios_sistema"))
            except (AppError, ValidationError) as exc:
                error = exc.message if isinstance(exc, AppError) else str(exc)
            except Exception:  # noqa: BLE001
                error = "No se pudo actualizar el usuario. Revisa los datos e inténtalo de nuevo."
        valores = dict(request.form)
        valores["id"] = usuario_id
    else:
        try:
            valores = rest_auth_service.obtener_usuario(usuario_id)
        except (AppError, ValidationError) as exc:
            flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
            return redirect(url_for("auth.usuarios_sistema"))

    return render_template(
        "auth/usuarios_sistema_editar.html",
        error=error,
        usuario=valores,
        es_mismo=usuario_id == session.get("usuario_id"),
    )


@bp.post("/sistema/usuarios/<int:usuario_id>/cambiar_estado")
@login_required
@admin_required
def usuario_cambiar_estado(usuario_id):
    """Activa/desactiva un usuario (evita desactivar la propia cuenta)."""
    if usuario_id == session.get("usuario_id"):
        flash("No puedes desactivar tu propia cuenta.", "error")
        return redirect(url_for("auth.usuarios_sistema"))
    try:
        nuevo = rest_auth_service.cambiar_estado(usuario_id)
        flash(
            "Usuario activado correctamente." if nuevo else "Usuario desactivado correctamente.",
            "success",
        )
    except (AppError, ValidationError) as exc:
        flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
    except Exception:  # noqa: BLE001
        flash("No se pudo cambiar el estado del usuario.", "error")
    return redirect(url_for("auth.usuarios_sistema"))