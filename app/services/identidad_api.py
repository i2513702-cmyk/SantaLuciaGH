"""Consulta de documentos de identidad peruanos vía API externas.

Proveedores soportados (todo es HTTPS + Bearer token, sin dependencias nuevas;
se usa `urllib` de la biblioteca estándar, igual que el resto del proyecto):

| Documento | apiperu.dev  | json.pe                  |
|-----------|--------------|--------------------------|
| DNI       | POST /dni    | POST /api/dni (fallback) |
| RUC       | POST /ruc    | POST /api/ruc (fallback) |
| CE        | —            | POST /api/ce             |

El pasaporte peruano NO tiene proveedor de consulta automática entre las APIs
comerciales (solo la web de Migraciones, con captcha): el sistema registra el
pasaporte manualmente sin autollenado.

La respuesta se normaliza a un solo formato para que el frontend (registro,
citas, admin) siempre reciba los mismos campos.
"""

import json
import logging
import re
import urllib.error
import urllib.request

from app.config import Config
from app.exceptions import AppError, ValidationError

log = logging.getLogger(__name__)

TIPO_DNI = "DNI"
TIPO_CE = "CE"
TIPO_RUC = "RUC"
TIPO_PASAPORTE = "PASAPORTE"

TIPOS_SOPORTADOS = {TIPO_DNI, TIPO_CE, TIPO_RUC}

_FORMATOS = {
    TIPO_DNI: (r"^\d{8}$", "el DNI debe tener 8 dígitos"),
    TIPO_CE: (r"^\d{8,9}$", "el carnet de extranjería debe tener 8 o 9 dígitos"),
    TIPO_RUC: (r"^\d{11}$", "el RUC debe tener 11 dígitos"),
}

# Cada tipo tiene una lista de (proveedor, url, var_token). Si el primero falla
# o no está configurado se intenta el siguiente (fallback).
_PROVEEDORES = {
    TIPO_DNI: [
        ("apiperu", Config.APIPERU_DNI_URL, "APIPERU_TOKEN"),
        ("jsonpe", Config.JSONPE_DNI_URL, "JSONPE_TOKEN"),
    ],
    TIPO_RUC: [
        ("apiperu", Config.APIPERU_RUC_URL, "APIPERU_TOKEN"),
        ("jsonpe", Config.JSONPE_RUC_URL, "JSONPE_TOKEN"),
    ],
    TIPO_CE: [
        ("jsonpe", Config.JSONPE_CE_URL, "JSONPE_TOKEN"),
    ],
}


class _ProveedorError(Exception):
    """Fallo de un proveedor; se intenta con el siguiente de la lista."""

    def __init__(self, mensaje: str, status: int = 0):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status = status


def _pedir_con_token(url: str, campo: str, numero: str, token: str) -> dict:
    """POST JSON con el Bearer token del proveedor y devuelve el JSON."""
    cabeceras = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    body = json.dumps({campo: numero}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=cabeceras, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=Config.IDENTIDAD_TIMEOUT) as resp:
            crudo = resp.read()
            return json.loads(crudo) if crudo else {}
    except urllib.error.HTTPError as exc:
        crudo = exc.read()
        try:
            data = json.loads(crudo) if crudo else {}
        except ValueError:
            data = {}
        mensaje = data.get("message") or data.get("error") or f"HTTP {exc.code}"
        raise _ProveedorError(mensaje, exc.code) from None
    except urllib.error.URLError as exc:
        raise _ProveedorError(f"No hay respuesta del proveedor: {exc.reason}") from None
    except (TimeoutError, OSError) as exc:
        raise _ProveedorError(f"Timeout o error de red: {exc}") from None
    except ValueError:
        raise _ProveedorError("La respuesta del proveedor no es JSON válido") from None


def _extraer(proveedor: str, tipo: str, numero: str, resp: dict) -> dict:
    """Convierte la respuesta cruda del proveedor al formato normalizado."""
    datos = resp.get("data") or {}
    salida = {
        "tipo_documento": tipo,
        "numero": numero,
        "proveedor": proveedor,
        "nombres": "",
        "apellido_paterno": "",
        "apellido_materno": "",
        "nombre_completo": "",
        "razon_social": "",
        "estado": "",
        "condicion": "",
        "direccion": "",
    }

    if tipo in (TIPO_DNI, TIPO_CE):
        salida.update({
            "nombres": datos.get("nombres") or "",
            "apellido_paterno": datos.get("apellido_paterno") or "",
            "apellido_materno": datos.get("apellido_materno") or "",
            "nombre_completo": (
                datos.get("nombre_completo")
                or datos.get("full_name")
                or ""
            ),
        })
        if not salida["nombre_completo"]:
            salida["nombre_completo"] = " ".join(
                [
                    salida["apellido_paterno"],
                    salida["apellido_materno"],
                    salida["nombres"],
                ]
            ).strip()
    elif tipo == TIPO_RUC:
        salida.update({
            "razon_social": (
                datos.get("nombre_o_razon_social")
                or datos.get("razon_social")
                or datos.get("nombre")
                or ""
            ),
            "estado": datos.get("estado") or "",
            "condicion": datos.get("condicion") or "",
            "direccion": datos.get("direccion") or "",
        })
        salida["nombre_completo"] = salida["razon_social"]
    return salida


def consultar(tipo: str, numero: str) -> dict:
    """Consulta un documento (DNI/CE/RUC) y devuelve los datos normalizados.

    Lanza ValidationError si el formato es inválido (o el tipo no se puede
    consultar) y AppError(503) si ningún proveedor configurado responde.
    """
    tipo = (tipo or "").strip().upper()
    numero = (numero or "").strip()

    if tipo == TIPO_PASAPORTE:
        raise ValidationError(
            "El pasaporte se registra manualmente: no existe API de consulta "
            "para pasaportes peruanos."
        )
    if tipo not in TIPOS_SOPORTADOS:
        raise ValidationError(
            f"Tipo de documento no soportado para autollenado: {tipo or 'vacío'}."
            " Soportados: DNI, RUC, CE."
        )

    patron, mensaje_formato = _FORMATOS[tipo]
    if not re.match(patron, numero):
        raise ValidationError(f"Número inválido: {mensaje_formato}.")

    errores = []
    for proveedor, url, var_token in _PROVEEDORES[tipo]:
        token = getattr(Config, var_token, "")
        if not url or not token:
            errores.append(f"{proveedor}: sin token/URL configurados")
            continue
        try:
            resp = _pedir_con_token(url, tipo.lower(), numero, token)
            if resp.get("success") is False:
                msg = resp.get("message") or resp.get("code") or "Documento no encontrado."
                retryable = bool(resp.get("retryable"))
                raise _ProveedorError(msg, 503 if retryable else 0)
            if not resp.get("data"):
                raise _ProveedorError("El proveedor no devolvió datos para el documento.")
            return _extraer(proveedor, tipo, numero, resp)
        except _ProveedorError as exc:
            errores.append(f"{proveedor}: {exc.mensaje}")
            log.warning("Consulta %s %s falló en %s: %s", tipo, numero, proveedor, exc.mensaje)

    detalles = " · ".join(errores) or "Sin proveedores configurados."
    if "sin token/URL" in detalles:
        raise AppError(
            "La consulta de documentos no está configurada: pega tu token en "
            "el .env (APIPERU_TOKEN / JSONPE_TOKEN).",
            status_code=503,
        )
    raise AppError(
        f"No se pudo consultar el {tipo}: {detalles}",
        status_code=503,
    )