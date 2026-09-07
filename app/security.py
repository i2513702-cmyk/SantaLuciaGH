"""Utilidades de seguridad: hasheo y verificación de contraseñas con bcrypt.

Inspirado en el patrón del proyecto de referencia (flask-bcrypt):
  - hash_password(clave)    -> generar hash bcrypt
  - check_password(hash, x) -> verificar contraseña contra el hash

Nota: los hashes generados con werkzeug (pbkdf2) aún pueden verificarse
cambiando a la función correspondiente, pero el estándar del proyecto
a partir de ahora es bcrypt.
"""

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