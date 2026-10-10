"""Consulta de identidad (DNI/RUC/CE) para autollenado de formularios.

Endpoints:

- POST /identidad/consultar          — público (registro, citas): consulta el
  documento en apiperu.dev / json.pe y devuelve nombres o razón social.
- POST /identidad/consultar-empleado — solo admin: consulta el documento y
  además busca en la tabla `empleados` el id para vincular el usuario.

Ambos son POST y pasan por la protección CSRF global (cabecera X-CSRF-Token o
campo csrf_token), así que un tercero no puede gastar consultas a tu plan
desde otro sitio.
"""

from flask import Blueprint, jsonify, request

from app.decorators import admin_required, login_required
from app.exceptions import AppError, ValidationError
from app.services import identidad_api
from app.supabase_client import get_reader

bp = Blueprint("identidad", __name__)


def _cuerpo():
    """Tipo y número de documento del payload JSON."""
    body = request.get_json(silent=True) or {}
    tipo = (body.get("tipo") or "").strip().upper()
    numero = (body.get("numero") or "").strip()
    return tipo, numero


def _respuesta_error(exc: AppError):
    """JSON de error con el status del AppError."""
    return jsonify(exc.to_dict()), exc.status_code


@bp.post("/identidad/consultar")
def consultar():
    """POST /identidad/consultar — datos de un documento (autollenado)."""
    tipo, numero = _cuerpo()
    try:
        return jsonify({"datos": identidad_api.consultar(tipo, numero)}), 200
    except (AppError, ValidationError) as exc:
        return _respuesta_error(exc)


@bp.post("/identidad/consultar-empleado")
@login_required
@admin_required
def consultar_empleado():
    """POST /identidad/consultar-empleado — busca un empleado por documento.

    Para el panel de Administración: al ingresar un DNI autollenamos la ficha
    del documento y, si la persona ya está en la tabla `empleados`, devolvemos
    su id para vincular el usuario nuevo.
    """
    tipo, numero = _cuerpo()
    try:
        datos = identidad_api.consultar(tipo, numero)
    except (AppError, ValidationError) as exc:
        return _respuesta_error(exc)

    empleado = None
    try:
        resp = (
            get_reader()
            .table("empleados")
            .select("id,nombres,apellidos,cargo,correo")
            .eq("numero_documento", numero)
            .limit(1)
            .execute()
        )
        empleado = (resp.data or [None])[0]
    except Exception:  # noqa: BLE001
        empleado = None

    return jsonify({
        "datos": datos,
        "empleado": empleado,
        "encontrado": bool(empleado),
    }), 200