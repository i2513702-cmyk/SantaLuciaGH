"""Panel de seguridad: IPs con intentos fallidos y bloqueadas (solo ADMINISTRADOR)."""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.decorators import roles_required
from app.services import cuenta_guard, login_guard

bp = Blueprint("seguridad", __name__)


@bp.get("/admin/seguridad")
@roles_required("admin")
def panel():
    ips = login_guard.listar()
    return render_template(
        "admin/seguridad.html",
        ips=ips,
        n_bloqueadas=sum(1 for i in ips if i.get("bloqueada")),
        cuentas=cuenta_guard.listar(),
        eventos=login_guard.listar_eventos(50),
        cuenta_max=cuenta_guard.Config.CUENTA_MAX_INTENTOS,
        cuenta_min=cuenta_guard.Config.CUENTA_BLOQUEO_MIN,
        max_intentos=login_guard.Config.LOGIN_MAX_INTENTOS,
        espera_min=login_guard.Config.LOGIN_ESPERA_MIN,
        fallos_bloqueo=login_guard.Config.LOGIN_FALLOS_BLOQUEO,
    )


@bp.post("/admin/seguridad/desbloquear")
@roles_required("admin")
def desbloquear():
    ip = request.form.get("ip", "").strip()
    if ip:
        login_guard.desbloquear(ip, session.get("username", "admin"))
        flash(f"IP {ip} desbloqueada.", "success")
    return redirect(url_for("seguridad.panel"))


@bp.post("/admin/seguridad/bloquear")
@roles_required("admin")
def bloquear():
    ip = request.form.get("ip", "").strip()
    if ip and ip != request.remote_addr:
        login_guard.bloquear(ip, session.get("username", "admin"))
        flash(f"IP {ip} bloqueada.", "warning")
    elif ip:
        flash("No puedes bloquear tu propia IP.", "error")
    return redirect(url_for("seguridad.panel"))


@bp.post("/admin/seguridad/cuentas/desbloquear")
@roles_required("admin")
def desbloquear_cuenta():
    usuario_id = request.form.get("usuario_id", "").strip()
    nombre = request.form.get("nombre_usuario", "").strip()
    if usuario_id.isdigit():
        if cuenta_guard.desbloquear(int(usuario_id), session.get("username", "admin"), nombre):
            flash(f"Cuenta {nombre or usuario_id} desbloqueada.", "success")
        else:
            flash("No se pudo desbloquear la cuenta. Revisa que el SQL 002 esté aplicado.", "error")
    return redirect(url_for("seguridad.panel"))
