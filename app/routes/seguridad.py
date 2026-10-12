"""Panel de seguridad: IPs con intentos fallidos y bloqueadas (solo ADMINISTRADOR)."""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.decorators import roles_required
from app.services import login_guard

bp = Blueprint("seguridad", __name__)


@bp.get("/admin/seguridad")
@roles_required("admin")
def panel():
    ips = login_guard.listar()
    return render_template(
        "admin/seguridad.html",
        ips=ips,
        n_bloqueadas=sum(1 for i in ips if i.get("bloqueada")),
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
