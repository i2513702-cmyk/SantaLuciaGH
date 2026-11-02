"""Mensajes de error amigables para el usuario.

El detalle tecnico se registra con log.exception; nunca se muestra al usuario.
"""
import logging

log = logging.getLogger(__name__)

_MENSAJES = {
    "23514": "Los datos no cumplen las reglas de la base de datos.",
    "23505": "Ya existe un registro con esos datos.",
    "23503": "Referencia invalida: verifica los datos relacionados.",
    "42501": "No tienes permisos para realizar esta accion.",
    "PGRST204": "Falta una columna en la base de datos; contacta al administrador.",
}


def mensaje_amigable(exc) -> str:
    """Devuelve un mensaje claro en espanol para el usuario."""
    texto = str(exc)
    for codigo, mensaje in _MENSAJES.items():
        if codigo in texto:
            log.exception("Error tecnico mapeado (%s): %s", codigo, exc)
            return mensaje
    log.exception("Error tecnico no mapeado: %s", exc)
    return "Ocurrio un error. Intentalo de nuevo."
