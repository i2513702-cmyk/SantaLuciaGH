"""API REST pública/privada del sistema Santa Lucía (base /api/v1).

La API se registra como un blueprint independiente del portal HTML. Los
módulos de este paquete implementan los endpoints E01–E78 del informe.
"""

from flask import Blueprint, jsonify, request

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


@bp.errorhandler(404)
def _api_no_encontrado(_):
    return (
        jsonify(
            {
                "codigo": "NO_ENCONTRADO",
                "mensaje": "El recurso solicitado no existe.",
                "error": "El recurso solicitado no existe.",
                "status_code": 404,
            }
        ),
        404,
    )


@bp.errorhandler(405)
def _api_metodo_no_permitido(_):
    return (
        jsonify(
            {
                "codigo": "METODO_NO_PERMITIDO",
                "mensaje": "El método HTTP no está permitido para esta ruta.",
                "error": "El método HTTP no está permitido para esta ruta.",
                "status_code": 405,
            }
        ),
        405,
    )


# Importar al final para que los módulos puedan hacer `from app.api import bp`.
from app.api import admin, appointments, auth, cart, me, orders, public, staff  # noqa: E402,F401
