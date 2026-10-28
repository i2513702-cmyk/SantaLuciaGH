"""Rutas públicas y administrativas para el Libro de Reclamaciones (Ley 29571 - Perú)."""

import csv
import io
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Dict, Optional

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from markupsafe import Markup

from app.decorators import roles_required
from app.exceptions import AppError, ValidationError
from app.services import libro_reclamaciones_service
from app.supabase_client import get_admin_client, get_reader

bp = Blueprint("libro_reclamaciones", __name__)

PROVEEDOR = {
    "razon_social": "Santa Lucia Servicio Técnico SAC",
    "ruc": "20601234567",
    "domicilio": "Av. 28 de Julio 723 - Chilca - Huancayo",
}

EMAIL_RECLAMOS = "contacto@santalucia.pe"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validar_captcha() -> bool:
    """Validación simple anti-spam. Requiere configurar RECAPTCHA_SECRET si se usa Google."""
    # Si no hay captcha configurado, permitir solo en dev con verificación básica
    token = request.form.get("g-recaptcha-response") or request.form.get("h-captcha-response")
    secret = None
    try:
        from app import create_app

        app = create_app()
        secret = app.config.get("RECAPTCHA_SECRET") or app.config.get("HCAPTCHA_SECRET")
    except Exception:
        pass
    if secret and token:
        try:
            import requests

            resp = requests.post(
                "https://www.google.com/recaptcha/api/siteverify",
                data={"secret": secret, "response": token},
                timeout=5,
            )
            return resp.json().get("success", False)
        except Exception:
            return False
    # Sin captcha configurado - validación básica
    if token and len(token) < 10:
        return False
    return True


@bp.route("/libro-de-reclamaciones")
def formulario():
    """Formulario público del Libro de Reclamaciones."""
    hoy = _now().strftime("%Y-%m-%d")
    hora = _now().strftime("%H:%M")
    return render_template(
        "libro_reclamaciones/formulario.html",
        proveedor=PROVEEDOR,
        fecha=hoy,
        hora=hora,
        email_reclamos=EMAIL_RECLAMOS,
    )


@bp.route("/libro-de-reclamaciones/enviar", methods=["POST"])
def enviar():
    """Envía un reclamo/queja."""
    try:
        if not _validar_captcha():
            flash("Verificación anti-spam fallida. Inténtalo de nuevo.", "error")
            return redirect(url_for("libro_reclamaciones.formulario"))

        datos = {
            "fecha_registro": _now().isoformat(),
            "razon_social": PROVEEDOR["razon_social"],
            "ruc": PROVEEDOR["ruc"],
            "domicilio_proveedor": PROVEEDOR["domicilio"],
            # Consumidor
            "consumidor_nombres": (request.form.get("consumidor_nombres") or "").strip(),
            "consumidor_apellidos": (request.form.get("consumidor_apellidos") or "").strip(),
            "tipo_documento": (request.form.get("tipo_documento") or "").strip().upper(),
            "numero_documento": (request.form.get("numero_documento") or "").strip(),
            "domicilio_consumidor": (request.form.get("domicilio_consumidor") or "").strip(),
            "telefono": (request.form.get("telefono") or "").strip(),
            "email": (request.form.get("email") or "").strip().lower(),
            # Menor de edad
            "es_menor": request.form.get("es_menor") == "on",
            "apoderado_nombre": (request.form.get("apoderado_nombre") or "").strip(),
            "apoderado_parentesco": (request.form.get("apoderado_parentesco") or "").strip(),
            "apoderado_documento": (request.form.get("apoderado_documento") or "").strip(),
            # Bien contratado
            "tipo_bien": (request.form.get("tipo_bien") or "").strip(),
            "descripcion_bien": (request.form.get("descripcion_bien") or "").strip(),
            "monto_reclamado": (request.form.get("monto_reclamado") or "").strip(),
            # Detalle
            "tipo_solicitud": (request.form.get("tipo_solicitud") or "").strip().upper(),
            "detalle_hecho": (request.form.get("detalle_hecho") or "").strip(),
            "pedido_consumidor": (request.form.get("pedido_consumidor") or "").strip(),
            # Privacidad
            "acepta_privacidad": request.form.get("acepta_privacidad") == "on",
        }

        # Validaciones
        if not datos["acepta_privacidad"]:
            raise ValidationError("Debes aceptar la política de privacidad y tratamiento de datos personales.")
        if not all(
            [
                datos["consumidor_nombres"],
                datos["consumidor_apellidos"],
                datos["tipo_documento"],
                datos["numero_documento"],
                datos["domicilio_consumidor"],
                datos["telefono"],
                datos["email"],
                datos["tipo_bien"],
                datos["descripcion_bien"],
                datos["tipo_solicitud"],
                datos["detalle_hecho"],
                datos["pedido_consumidor"],
            ]
        ):
            raise ValidationError("Completa todos los campos obligatorios.")

        if datos["tipo_documento"] == "DNI" and not datos["numero_documento"].isdigit() or len(datos["numero_documento"]) != 8:
            raise ValidationError("El DNI debe tener 8 dígitos numéricos.")
        if "@" not in datos["email"] or "." not in datos["email"].split("@")[-1]:
            raise ValidationError("El correo electrónico no es válido.")
        if not datos["telefono"].replace("+", "").replace(" ", "").isdigit() or len(datos["telefono"].replace("+", "").replace(" ", "")) < 9:
            raise ValidationError("El teléfono no es válido.")

        if datos["es_menor"]:
            if not all([datos["apoderado_nombre"], datos["apoderado_parentesco"], datos["apoderado_documento"]]):
                raise ValidationError("Si el consumidor es menor de edad, debes completar los datos del padre/madre/apoderado.")

        if datos["tipo_solicitud"] not in ("RECLAMO", "QUEJA"):
            raise ValidationError("El tipo debe ser RECLAMO o QUEJA.")

        reclamo = libro_reclamaciones_service.crear_reclamo(datos)

        # Enviar emails (si hay configuración SMTP)
        try:
            libro_reclamaciones_service.enviar_emails(reclamo)
        except Exception:
            pass

        flash(f"Tu reclamo ha sido registrado correctamente. N° de registro: {reclamo['numero_registro']}", "success")
        return render_template(
            "libro_reclamaciones/confirmacion.html",
            reclamo=reclamo,
            proveedor=PROVEEDOR,
        )
    except (AppError, ValidationError) as exc:
        flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
        return redirect(url_for("libro_reclamaciones.formulario"))
    except Exception:
        flash("Ocurrió un error al enviar el reclamo. Inténtalo nuevamente.", "error")
        return redirect(url_for("libro_reclamaciones.formulario"))


@bp.route("/admin/libro-de-reclamaciones")
@roles_required("admin")
def admin_listado():
    """Listado administrativo de reclamos."""
    estado = request.args.get("estado", "").strip()
    tipo = request.args.get("tipo", "").strip()
    fecha_desde = request.args.get("fecha_desde", "").strip()
    fecha_hasta = request.args.get("fecha_hasta", "").strip()
    q = request.args.get("q", "").strip()

    reclamos = libro_reclamaciones_service.listar_reclamos(
        estado=estado or None,
        tipo=tipo or None,
        fecha_desde=fecha_desde or None,
        fecha_hasta=fecha_hasta or None,
        q=q or None,
    )

    # Alertas de vencimiento (30 días)
    hoy = _now()
    for r in reclamos:
        try:
            fecha_reg = datetime.fromisoformat(str(r.get("fecha_registro")).replace("Z", "+00:00"))
            if fecha_reg.tzinfo is None:
                fecha_reg = fecha_reg.replace(tzinfo=timezone.utc)
            r["dias_transcurridos"] = (hoy - fecha_reg).days
            r["vence_en"] = 30 - r["dias_transcurridos"]
            r["proximo_vencer"] = r["vence_en"] <= 7 and r.get("estado") in ("pendiente", "en_proceso")
        except Exception:
            r["dias_transcurridos"] = 0
            r["vence_en"] = 30
            r["proximo_vencer"] = False

    return render_template(
        "admin/libro_reclamaciones/listado.html",
        reclamos=reclamos,
        estado_filtro=estado,
        tipo_filtro=tipo,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        q=q,
    )


@bp.route("/admin/libro-de-reclamaciones/<int:reclamo_id>")
@roles_required("admin")
def admin_detalle(reclamo_id):
    reclamo = libro_reclamaciones_service.obtener_reclamo(reclamo_id)
    if not reclamo:
        flash("Reclamo no encontrado.", "error")
        return redirect(url_for("libro_reclamaciones.admin_listado"))
    return render_template("admin/libro_reclamaciones/detalle.html", reclamo=reclamo)


@bp.route("/admin/libro-de-reclamaciones/<int:reclamo_id>/responder", methods=["POST"])
@roles_required("admin")
def admin_responder(reclamo_id):
    try:
        estado = (request.form.get("estado") or "").strip()
        respuesta = (request.form.get("respuesta") or "").strip()
        if estado not in ("pendiente", "en_proceso", "atendido"):
            raise ValidationError("Estado inválido.")
        if not respuesta and estado == "atendido":
            raise ValidationError("Debes registrar la respuesta al atender el reclamo.")
        libro_reclamaciones_service.responder_reclamo(reclamo_id, estado, respuesta)
        try:
            reclamo = libro_reclamaciones_service.obtener_reclamo(reclamo_id)
            libro_reclamaciones_service.enviar_email_respuesta(reclamo)
        except Exception:
            pass
        flash("Reclamo actualizado correctamente.", "success")
    except (AppError, ValidationError) as exc:
        flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
    except Exception:
        flash("No se pudo actualizar el reclamo.", "error")
    return redirect(url_for("libro_reclamaciones.admin_detalle", reclamo_id=reclamo_id))


@bp.route("/admin/libro-de-reclamaciones/exportar.csv")
@roles_required("admin")
def exportar_csv():
    reclamos = libro_reclamaciones_service.listar_reclamos()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "N° Registro",
            "Fecha",
            "Tipo",
            "Consumidor",
            "Documento",
            "Email",
            "Teléfono",
            "Bien",
            "Monto",
            "Detalle",
            "Pedido",
            "Estado",
            "Respuesta",
            "Fecha Respuesta",
        ]
    )
    for r in reclamos:
        writer.writerow(
            [
                r.get("numero_registro"),
                r.get("fecha_registro"),
                r.get("tipo_solicitud"),
                f"{r.get('consumidor_nombres')} {r.get('consumidor_apellidos')}",
                f"{r.get('tipo_documento')}: {r.get('numero_documento')}",
                r.get("email"),
                r.get("telefono"),
                r.get("descripcion_bien"),
                r.get("monto_reclamado"),
                r.get("detalle_hecho"),
                r.get("pedido_consumidor"),
                r.get("estado"),
                r.get("respuesta"),
                r.get("fecha_respuesta"),
            ]
        )
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"libro_reclamaciones_{datetime.now().strftime('%Y%m%d')}.csv",
    )


@bp.route("/admin/libro-de-reclamaciones/exportar.xlsx")
@roles_required("admin")
def exportar_xlsx():
    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError:
        flash("openpyxl no está instalado. Instálalo con: pip install openpyxl", "error")
        return redirect(url_for("libro_reclamaciones.admin_listado"))

    reclamos = libro_reclamaciones_service.listar_reclamos()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Libro de Reclamaciones"
    headers = [
        "N° Registro",
        "Fecha",
        "Tipo",
        "Consumidor",
        "Tipo Doc",
        "N° Doc",
        "Email",
        "Teléfono",
        "Domicilio Consumidor",
        "Tipo Bien",
        "Descripción Bien",
        "Monto Reclamado",
        "Detalle del Hecho",
        "Pedido del Consumidor",
        "Es Menor",
        "Apoderado",
        "Estado",
        "Respuesta",
        "Fecha Respuesta",
    ]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True)
    for row, r in enumerate(reclamos, 2):
        ws.cell(row=row, column=1, value=r.get("numero_registro"))
        ws.cell(row=row, column=2, value=r.get("fecha_registro"))
        ws.cell(row=row, column=3, value=r.get("tipo_solicitud"))
        ws.cell(row=row, column=4, value=f"{r.get('consumidor_nombres')} {r.get('consumidor_apellidos')}")
        ws.cell(row=row, column=5, value=r.get("tipo_documento"))
        ws.cell(row=row, column=6, value=r.get("numero_documento"))
        ws.cell(row=row, column=7, value=r.get("email"))
        ws.cell(row=row, column=8, value=r.get("telefono"))
        ws.cell(row=row, column=9, value=r.get("domicilio_consumidor"))
        ws.cell(row=row, column=10, value=r.get("tipo_bien"))
        ws.cell(row=row, column=11, value=r.get("descripcion_bien"))
        ws.cell(row=row, column=12, value=r.get("monto_reclamado"))
        ws.cell(row=row, column=13, value=r.get("detalle_hecho"))
        ws.cell(row=row, column=14, value=r.get("pedido_consumidor"))
        ws.cell(row=row, column=15, value="SI" if r.get("es_menor") else "NO")
        ws.cell(row=row, column=16, value=r.get("apoderado_nombre") or "")
        ws.cell(row=row, column=17, value=r.get("estado"))
        ws.cell(row=row, column=18, value=r.get("respuesta"))
        ws.cell(row=row, column=19, value=r.get("fecha_respuesta"))
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except Exception:
                pass
        adjusted_width = min(max_length + 2, 50)
        ws.column_dimensions[column].width = adjusted_width
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"libro_reclamaciones_{datetime.now().strftime('%Y%m%d')}.xlsx",
    )
