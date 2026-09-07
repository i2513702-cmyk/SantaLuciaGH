"""Autenticación del portal HTML contra la tabla `usuarios` de Supabase (PostgREST).

Mismo patrón que el app.py de referencia: el login consulta la BD, valida la
contraseña con bcrypt y guarda la sesión. La diferencia: la "BD" es la API
REST de Supabase usando los tokens del .env.

Escrituras (crear usuario, cambiar clave) usan SUPABASE_SERVICE_KEY porque la
publishable key no puede escribir (RLS activa en el proyecto).
"""

from app.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.security import check_password, hash_password
from app.supabase_client import (
    error_postgrest,
    get_admin_client,
    get_reader,
)

TABLA_USUARIOS = "usuarios"


def _leer_usuario(campo: str, valor: str):
    """Consulta la tabla usuarios por un campo y devuelve la fila o None."""
    try:
        resp = (
            get_reader()
            .table(TABLA_USUARIOS)
            .select("*")
            .eq(campo, valor)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    return resp.data[0] if resp.data else None


def _buscar_usuario(identificador: str):
    """Busca un usuario por nombre_usuario O correo en una sola consulta."""
    try:
        resp = (
            get_reader()
            .table(TABLA_USUARIOS)
            .select("*")
            .or_(
                f"nombre_usuario.eq.{identificador},"
                f"correo.eq.{identificador}"
            )
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as exc:  # noqa: BLE001
        # Fallback por separado si el filtro OR falla (caracteres especiales).
        usuario = _leer_usuario("nombre_usuario", identificador)
        if not usuario:
            usuario = _leer_usuario("correo", identificador)
        return usuario


def _tabla_escritura():
    return get_admin_client().table(TABLA_USUARIOS)


def login(identificador: str, password: str) -> dict:
    """Valida credenciales contra Supabase y devuelve la fila del usuario."""
    if not identificador or not password:
        raise ValidationError("Usuario y contraseña son obligatorios")

    usuario = _buscar_usuario(identificador)
    if not usuario:
        raise AuthorizationError("Usuario o contraseña incorrectos.")

    if usuario.get("activo") is False:
        raise AuthorizationError("El usuario está inactivo.")

    if not check_password(usuario.get("password", ""), password):
        raise AuthorizationError("Usuario o contraseña incorrectos.")

    return usuario


def listar_usuarios() -> list:
    """Devuelve todos los usuarios del sistema (para el panel de administración)."""
    try:
        resp = get_reader().table(TABLA_USUARIOS).select("*").order("id").execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    return resp.data or []


def listar_tipos_documento() -> list:
    """Devuelve los tipos de documento disponibles (DNI, RUC, etc.)."""
    try:
        resp = get_reader().table("tipo_documento").select("id, codigo, nombre").order("id").execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    return resp.data or []


def crear_usuario(
    nombre_usuario: str,
    correo: str,
    password: str,
    rol: str,
    empleado_id: int,
) -> dict:
    """Crea un usuario en Supabase con la contraseña hasheada con bcrypt."""
    if not password or len(password) < 8:
        raise ValidationError("La contraseña debe tener al menos 8 caracteres")

    if _leer_usuario("nombre_usuario", nombre_usuario):
        raise ValidationError(f"El nombre de usuario '{nombre_usuario}' ya existe.")
    if _leer_usuario("correo", correo):
        raise ValidationError(f"El correo '{correo}' ya está registrado.")

    fila = {
        "empleado_id": empleado_id,
        "rol": rol,
        "nombre_usuario": nombre_usuario,
        "correo": correo,
        "password": hash_password(password),
        "activo": True,
    }
    try:
        resp = _tabla_escritura().insert(fila).execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    return resp.data[0]


def cambiar_clave(nombre_usuario: str, clave_nueva: str) -> None:
    """Actualiza la contraseña de un usuario (campo password como hash bcrypt)."""
    if not clave_nueva or len(clave_nueva) < 8:
        raise ValidationError("La nueva contraseña debe tener al menos 8 caracteres")

    try:
        _tabla_escritura().update({"password": hash_password(clave_nueva)}).eq(
            "nombre_usuario", nombre_usuario
        ).execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc


def register(
    nombre_usuario: str,
    correo: str,
    password: str,
    rol: str,
    nombres: str,
    apellidos: str,
    tipo_documento_id: int,
    numero_documento: str,
    telefono: str = "",
    cargo: str = "Empleado",
) -> dict:
    """Crea una cuenta completa: inserta el empleado y luego el usuario.

    La tabla `usuarios` exige `empleado_id` NOT NULL y UNIQUE, por eso hay que
    crear primero la persona en `empleados` y usar su id. Los campos son los de
    la BD (empleados + usuarios).
    """
    if not password or len(password) < 8:
        raise ValidationError("La contraseña debe tener al menos 8 caracteres")

    if _leer_usuario("nombre_usuario", nombre_usuario):
        raise ValidationError(f"El nombre de usuario '{nombre_usuario}' ya existe.")
    if _leer_usuario("correo", correo):
        raise ValidationError(f"El correo '{correo}' ya está registrado.")

    try:
        empleado = (
            get_admin_client()
            .table("empleados")
            .insert({
                "tipo_documento_id": tipo_documento_id,
                "numero_documento": numero_documento,
                "nombres": nombres,
                "apellidos": apellidos,
                "telefono": telefono or None,
                "correo": correo,
                "cargo": cargo,
            })
            .execute()
        ).data[0]
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"Error creando el empleado: {error_postgrest(exc)}") from exc

    return crear_usuario(
        nombre_usuario=nombre_usuario,
        correo=correo,
        password=password,
        rol=rol,
        empleado_id=empleado["id"],
    )


def actualizar_ultimo_acceso(nombre_usuario: str, fecha_iso: str) -> None:
    """Guarda la fecha del último acceso (best-effort, ignora errores)."""
    try:
        _tabla_escritura().update({"ultimo_acceso": fecha_iso}).eq(
            "nombre_usuario", nombre_usuario
        ).execute()
    except Exception:  # noqa: BLE001
        pass