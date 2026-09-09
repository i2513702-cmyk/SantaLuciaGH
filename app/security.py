"""Utilidades de seguridad: hasheo y verificación de contraseñas con bcrypt.

Inspirado en el patrón del proyecto de referencia (flask-bcrypt):
  - hash_password(clave)    -> generar hash bcrypt
  - check_password(hash, x) -> verificar contraseña contra el hash

Nota: los hashes generados con werkzeug (pbkdf2) aún pueden verificarse
cambiando a la función correspondiente, pero el estándar del proyecto
a partir de ahora es bcrypt.

También genera y valida el token CSRF de los formularios.
"""

import secrets

from flask import session

from app.extensions import bcrypt


def hash_password(clave: str) -> str:
    """Devuelve el hash bcrypt de la contraseña."""
    if not clave:
        raise ValueError("La contraseña no puede estar vacía")
    return bcrypt.generate_password_hash(clave).decode("utf-8")


def check_password(clave_en_hash: str, clave_plana: str) -> bool:
    """Verifica una contraseña contra su hash bcrypt."""
    if not clave_en_hash or not clave_plana:
        return False
    return bcrypt.check_password_hash(clave_en_hash, clave_plana)


def get_csrf() -> str:
    """Devuelve (y crea si hace falta) el token CSRF de la sesión."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(16)
        session["csrf_token"] = token
    return token


def csrf_valido(token) -> bool:
    """True si el token coincide con el de la sesión (comparación segura)."""
    esperado = session.get("csrf_token") or ""
    return bool(token and esperado and secrets.compare_digest(token, esperado))