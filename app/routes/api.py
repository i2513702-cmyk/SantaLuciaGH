"""API REST interna para el autocompletado del formulario de clientes.

Devuelve solo los campos necesarios para rellenar el formulario (nombres,
apellidos, teléfono y correo). Nunca expone contraseñas ni datos de otros
clientes, y exige sesión iniciada: la misma política que el resto del portal.
"""

import logging
import re

from flask import Blueprint, jsonify, request

from app.decorators import login_required
from app.exceptions import AppError, ValidationError
from app.services import identidad_api
from app.supabase_client import error_postgrest, get_reader

log = logging.getLogger(__name__)

bp = Blueprint("api", __name__, url_prefix="/api")

# Solo dígitos: un documento nunca lleva letras ni símbolos.
_SOLO_DIGITOS = re.compile(r"^\d+$")

# Longitudes válidas por tipo de documento (Perú). Se usa como validación
# laxa: si el tipo no está en el mapa, se acepta cualquier longitud razonable.
LONGITUD_DOCUMENTO = {
    "DNI": (8, 8),
    "CE": (9, 12),
    "RUC": (11, 11),
    "PASAPORTE": (6, 12),
    "PT": (1, 15),
}

MIN_CARACTERES_BUSQUEDA = 6
MAX_CARACTERES_BUSQUEDA = 15

CAMPOS_CLIENTE = "id,nombres,apellidos,razon_social,telefono,correo,numero_documento"


def _codigo_tipo_documento() -> str:
    """Código del tipo de documento seleccionado (DNI, RUC, ...)."""
    tipo_id = (request.args.get("tipo_documento_id") or "").strip()
    if not tipo_id.isdigit():
        return ""
    try:
        fila = (
            get_reader()
            .table("tipo_documento")
            .select("codigo")
            .eq("id", int(tipo_id))
            .limit(1)
            .execute()
        ).data
    except Exception:  # noqa: BLE001
        return ""
    return ((fila or [{}])[0].get("codigo") or "").strip().upper()


def _validar_documento(numero: str, codigo_tipo: str) -> str:
    """Devuelve '' si el documento es válido, o el mensaje de error."""
    if not _SOLO_DIGITOS.match(numero):
        return "El número de documento solo puede contener dígitos."

    minimo, maximo = LONGITUD_DOCUMENTO.get(codigo_tipo, (MIN_CARACTERES_BUSQUEDA, MAX_CARACTERES_BUSQUEDA))
    if len(numero) < minimo or len(numero) > maximo:
        esperado = str(minimo) if minimo == maximo else f"{minimo} y {maximo}"
        etiqueta = codigo_tipo or "documento"
        return f"Un {etiqueta} debe tener entre {esperado} dígitos."
    return ""


def _normalizar(cliente: dict) -> dict:
    """Datos del cliente listos para el formulario."""
    return {
        "id": cliente.get("id"),
        "nombres": cliente.get("nombres") or "",
        "apellidos": cliente.get("apellidos") or "",
        "razon_social": cliente.get("razon_social") or "",
        "telefono": cliente.get("telefono") or "",
        "correo": cliente.get("correo") or "",
        "numero_documento": cliente.get("numero_documento") or "",
    }


def _inferir_tipo(numero: str, codigo_tipo: str) -> str:
    """Tipo consultable en la API externa (DNI/RUC/CE); '' si no se puede."""
    if codigo_tipo in ("DNI", "RUC", "CE"):
        return codigo_tipo
    if codigo_tipo:  # pasaporte u otro: no hay API de consulta
        return ""
    if len(numero) == 8:
        return "DNI"
    if len(numero) == 11:
        return "RUC"
    return ""


def _desde_api(numero: str, tipo: str) -> dict:
    """Consulta RENIEC/SUNAT (apiperu.dev / json.pe) y lo deja listo para el formulario."""
    datos = identidad_api.consultar(tipo, numero)
    if tipo == "RUC":
        nombres = datos.get("razon_social") or datos.get("nombre_completo") or ""
        apellidos = ""
    else:
        nombres = datos.get("nombres") or ""
        apellidos = " ".join(
            p for p in (datos.get("apellido_paterno"), datos.get("apellido_materno")) if p
        )
        if not nombres and not apellidos:
            nombres = datos.get("nombre_completo") or ""
    return {
        "id": None,
        "nombres": nombres,
        "apellidos": apellidos,
        "razon_social": datos.get("razon_social") or "",
        "telefono": "",
        "correo": "",
        "numero_documento": numero,
    }


@bp.get("/clientes/buscar/<numero_documento>")
@login_required
def buscar_cliente(numero_documento):
    """GET /api/clientes/buscar/<documento> — autocompletado por documento.

    200 -> cliente encontrado (se rellenan los campos del formulario).
    404 -> no existe: el usuario puede escribir sus datos manualmente.
    400 -> documento incompleto o con formato inválido: no se consulta la BD.
    502 -> Supabase no respondió o la tabla/clave no es válida.
    """
    numero = (numero_documento or "").strip()
    codigo_tipo = _codigo_tipo_documento()

    if len(numero) < MIN_CARACTERES_BUSQUEDA:
        return jsonify({
            "ok": False,
            "encontrado": False,
            "detalle": "El número de documento está incompleto.",
        }), 400

    if len(numero) > MAX_CARACTERES_BUSQUEDA:
        return jsonify({
            "ok": False,
            "encontrado": False,
            "detalle": "El número de documento es demasiado largo.",
        }), 400

    error = _validar_documento(numero, codigo_tipo)
    if error:
        return jsonify({"ok": False, "encontrado": False, "detalle": error}), 400

    try:
        consulta = get_reader().table("clientes").select(CAMPOS_CLIENTE).eq("numero_documento", numero)
        if codigo_tipo and request.args.get("tipo_documento_id", "").strip().isdigit():
            consulta = consulta.eq("tipo_documento_id", int(request.args["tipo_documento_id"]))
        fila = consulta.limit(1).execute().data
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "ok": False,
            "encontrado": False,
            "detalle": error_postgrest(exc),
        }), 502

    if not fila:
        # No esta en la BD: se consulta RENIEC/SUNAT para autocompletar igual.
        tipo_api = _inferir_tipo(numero, codigo_tipo)
        if tipo_api:
            try:
                cliente = _desde_api(numero, tipo_api)
                if cliente["nombres"] or cliente["apellidos"]:
                    return jsonify({
                        "ok": True,
                        "encontrado": False,
                        "origen": "api",
                        "cliente": cliente,
                    }), 200
            except (AppError, ValidationError) as exc:
                log.warning("Consulta externa %s %s fallo: %s", tipo_api, numero, exc)
            except Exception:  # noqa: BLE001
                log.exception("Consulta externa %s %s fallo", tipo_api, numero)
        return jsonify({
            "ok": True,
            "encontrado": False,
            "detalle": "No se encontraron datos del documento. Completa tus datos manualmente.",
        }), 404

    return jsonify({
        "ok": True,
        "encontrado": True,
        "cliente": _normalizar(fila[0]),
    }), 200