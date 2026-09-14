"""Agendamiento de citas de servicio técnico (requiere iniciar sesión)."""

from datetime import date

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.decorators import login_required
from app.services.cita_service import (
    CitaError,
    HORARIOS,
    agendar,
    datos_usuario,
    horarios_disponibles,
)
from app.supabase_client import get_reader

bp = Blueprint("citas", __name__)


def _opciones(tabla: str, cols: str = "*", orden: str = "nombre"):
    """Lista de opciones para selects (devuelve [] si la consulta falla)."""
    try:
        return (
            get_reader()
            .table(tabla)
            .select(cols)
            .order(orden)
            .execute()
        ).data or []
    except Exception:  # noqa: BLE001
        return []


def _referencias():
    return {
        "tipos": _opciones("tipos_electrodomestico", "id,nombre"),
        "marcas": _opciones("marcas", "id,nombre"),
        "documentos": _opciones("tipo_documento", "id,codigo,nombre"),
        "horarios": HORARIOS,
    }


def _ctx_agendar(**extra):
    """Contexto común del formulario de citas (con datos del usuario logueado)."""
    ctx = {
        **_referencias(),
        "tipo_atenciones": {"TALLER": "En taller (tú llevas el equipo)", "DOMICILIO": "A domicilio"},
        "today": date.today().isoformat(),
        "cliente": datos_usuario(session.get("usuario_id")),
        "disponibles": horarios_disponibles(date.today().isoformat()),
    }
    ctx.update(extra)
    return ctx


@bp.get("/citas")
@login_required
def agendar_form():
    return render_template("citas/agendar.html", **_ctx_agendar())


@bp.get("/citas/disponibilidad")
@login_required
def disponibilidad():
    """Devuelve JSON con las horas disponibles para una fecha dada."""
    fecha = (request.args.get("fecha") or "").strip()
    disponibles = horarios_disponibles(fecha) if fecha else []
    return jsonify({"fecha": fecha, "horarios": disponibles})


@bp.post("/citas")
@login_required
def agendar_cita():
    datos = {
        "tipo_documento_id": request.form.get("tipo_documento_id", "1"),
        "numero_documento": request.form.get("numero_documento", ""),
        "nombres": request.form.get("nombres", ""),
        "apellidos": request.form.get("apellidos", ""),
        "razon_social": request.form.get("razon_social", ""),
        "telefono": request.form.get("telefono", ""),
        "correo": request.form.get("correo", ""),
        "tipo_id": request.form.get("tipo_id", ""),
        "marca_id": request.form.get("marca_id", ""),
        "modelo": request.form.get("modelo", ""),
        "numero_serie": request.form.get("numero_serie", ""),
        "color": request.form.get("color", ""),
        "tipo_atencion": request.form.get("tipo_atencion", "TALLER"),
        "fecha_reserva": request.form.get("fecha_reserva", ""),
        "hora_inicio": request.form.get("hora_inicio", ""),
        "motivo": request.form.get("motivo", ""),
        "observaciones": request.form.get("observaciones", ""),
        "calle": request.form.get("calle", ""),
    }

    # Los datos personales siempre vienen del usuario logueado (no editables).
    datos_usuario_log = datos_usuario(session.get("usuario_id"))
    if datos_usuario_log:
        datos.update({
            "tipo_documento_id": str(datos_usuario_log["tipo_documento_id"]),
            "numero_documento": datos_usuario_log["numero_documento"],
            "nombres": datos_usuario_log["nombres"],
            "apellidos": datos_usuario_log["apellidos"],
            "telefono": datos_usuario_log["telefono"],
            "correo": datos_usuario_log["correo"],
        })

    try:
        resultado = agendar(datos)
    except CitaError as exc:
        return render_template(
            "citas/agendar.html",
            **_ctx_agendar(),
            error=str(exc),
            form=datos,
        ), 400

    flash("¡Tu cita de servicio técnico está agendada!", "success")
    return redirect(url_for("citas.confirmar", reserva_id=resultado["reserva_id"]))


@bp.get("/citas/confirmacion/<int:reserva_id>")
def confirmar(reserva_id):
    try:
        resp = (
            get_reader()
            .table("reservas")
            .select(
                "id,cliente_id,electrodomestico_id,tipo_atencion,fecha_reserva,"
                "hora_inicio,hora_fin,estado,motivo,observaciones,calle,"
                "electrodomesticos(modelo,numero_serie,marcas(nombre),tipos_electrodomestico(nombre))"
            )
            .eq("id", reserva_id)
            .limit(1)
            .execute()
        )
        reserva = (resp.data or [None])[0]
        if not reserva:
            flash("No se encontró la cita solicitada.", "error")
            return redirect(url_for("citas.agendar_form"))

        cliente = None
        try:
            cliente = (
                get_reader()
                .table("clientes")
                .select("id,nombres,apellidos,razon_social,telefono,correo")
                .eq("id", reserva.get("cliente_id"))
                .limit(1)
                .execute()
            ).data
            cliente = (cliente or [None])[0]
        except Exception:  # noqa: BLE001
            cliente = None

        return render_template("citas/confirmacion.html", reserva=reserva, cliente=cliente)
    except Exception:  # noqa: BLE001
        return render_template("errors/404.html"), 404