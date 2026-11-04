"""Endpoints de autenticación E14–E21."""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from flask import jsonify, request

from app.api import bp
from app.api.common import (
    ApiError,
    ACCESS_TTL,
    RESET_TTL,
    api_auth,
    api_route,
    correo_valido,
    crear_token,
    cuerpo,
    decodificar_token,
    emitir_sesion,
    entero,
    insert_row,
    meta_guardar,
    meta_obtener,
    now_utc_iso,
    perfil_persona,
    read_one,
    read_rows,
    requerir,
    texto,
    _refresh_vigente,
    _revocar_refresh,
    _token_autorizacion,
)
from app.exceptions import AuthorizationError, CuentaBloqueadaError
from app.security import hash_password
from app.services import rest_auth_service

log = logging.getLogger(__name__)


def _destino(rol: str) -> str:
    if rol == "ADMINISTRADOR":
        return "/admin"
    if rol in {"SUPERVISOR", "TECNICO", "RECEPCIONISTA", "VENDEDOR", "ALMACENERO"}:
        return "/panel"
    return "/"


def _password_valida(password: str, exigir_simbolo: bool = True) -> bool:
    if not password or len(password) < 8:
        return False
    if exigir_simbolo:
        if not any(c.isupper() for c in password):
            return False
        if not any(not c.isalnum() for c in password):
            return False
    return True


def _enviar_correo_reset(destino: str, token: str, nombre: str = "") -> None:
    """Envía el enlace de recuperación si el SMTP está configurado."""
    host = (os.getenv("MAIL_SERVER") or "").strip()
    usuario = (os.getenv("MAIL_USERNAME") or "").strip()
    clave = (os.getenv("MAIL_PASSWORD") or "").strip()
    if not (host and usuario and clave):
        return
    puerto = entero(os.getenv("MAIL_PORT"), 587)
    remitente = (os.getenv("MAIL_DEFAULT_SENDER") or usuario).strip()
    usar_tls = (os.getenv("MAIL_USE_TLS", "1") or "1").lower() not in {"0", "false", "no"}
    enlace = f"https://santalucia.pe/restablecer?token={token}"
    msg = EmailMessage()
    msg["Subject"] = "Recupera tu contraseña - Santa Lucía"
    msg["From"] = remitente
    msg["To"] = destino
    msg.set_content(
        f"Hola {nombre or ''},\n\n"
        "Recibimos una solicitud para restablecer tu contraseña. Usa este enlace "
        f"durante los próximos 30 minutos:\n\n{enlace}\n\n"
        "Si no solicitaste el cambio, puedes ignorar este correo."
    )
    try:
        contexto = ssl.create_default_context()
        if puerto == 465:
            with smtplib.SMTP_SSL(host, puerto, timeout=15, context=contexto) as server:
                server.login(usuario, clave)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, puerto, timeout=15) as server:
                if usar_tls:
                    server.starttls(context=contexto)
                server.login(usuario, clave)
                server.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo enviar el correo de recuperación: %s", exc)


@bp.post("/auth/login")
@api_route
def auth_login():
    data = cuerpo()
    identificador = requerir(data, "usuario")
    password = texto(data.get("password"))
    if not password:
        raise ApiError("La contraseña es obligatoria.", 400, "DATOS_INVALIDOS", {"password": "obligatorio"})
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    try:
        usuario = rest_auth_service.login(identificador, password, ip)
    except CuentaBloqueadaError as exc:
        raise ApiError(exc.message, 403, "CUENTA_BLOQUEADA") from exc
    except AuthorizationError as exc:
        raise ApiError("Usuario o contraseña incorrectos.", 401, "CREDENCIALES_INVALIDAS") from exc
    except Exception as exc:  # noqa: BLE001
        # rest_auth_service puede devolver ValidationError; el login siempre es 401.
        log.info("Login API rechazado: %s", exc)
        raise ApiError("Usuario o contraseña incorrectos.", 401, "CREDENCIALES_INVALIDAS") from exc

    try:
        rest_auth_service.actualizar_ultimo_acceso(usuario.get("nombre_usuario"), now_utc_iso())
    except Exception:  # noqa: BLE001
        pass

    rol = (usuario.get("rol") or "").upper()
    _, persona, nombre = perfil_persona(usuario)
    sesion = emitir_sesion(usuario)
    return jsonify(
        {
            **sesion,
            "usuario": {
                "id": usuario.get("id"),
                "nombre": nombre or usuario.get("nombre_usuario") or "",
                "rol": rol,
            },
            "destino": _destino(rol),
        }
    )


@bp.post("/auth/refresh")
@api_route
def auth_refresh():
    data = cuerpo()
    token = requerir(data, "refresh_token")
    payload = decodificar_token(token, "refresh")
    if not payload:
        raise ApiError("El refresh token es inválido o venció.", 401, "TOKEN_INVALIDO")
    usuario = read_one("usuarios", id=payload.get("sub"))
    if not usuario or usuario.get("activo") is False:
        raise ApiError("La cuenta no está disponible.", 401, "NO_AUTENTICADO")
    if not _refresh_vigente(payload.get("jti"), int(usuario["id"])):
        raise ApiError("El refresh token fue revocado.", 401, "TOKEN_REVOCADO")
    _revocar_refresh(payload.get("jti"))
    sesion = emitir_sesion(usuario)
    return jsonify(
        {
            "access_token": sesion["access_token"],
            "refresh_token": sesion["refresh_token"],
            "expires_in": sesion["expires_in"],
        }
    )


@bp.post("/auth/logout")
@api_route
@api_auth()
def auth_logout():
    data = cuerpo()
    token = texto(data.get("refresh_token"))
    if token:
        payload = decodificar_token(token, "refresh")
        if payload:
            _revocar_refresh(payload.get("jti"))
    return "", 204


@bp.get("/auth/me")
@api_route
@api_auth()
def auth_me():
    from app.api.common import contexto_actual

    ctx = contexto_actual()
    usuario = ctx.get("usuario") or {}
    _, persona, nombre = perfil_persona(usuario)
    return jsonify(
        {
            "id": usuario.get("id"),
            "nombres": persona.get("nombres") or nombre,
            "apellidos": persona.get("apellidos") or "",
            "usuario": usuario.get("nombre_usuario"),
            "correo": usuario.get("correo"),
            "rol": ctx.get("rol"),
            "permisos": ctx.get("permisos") or [],
        }
    )


@bp.post("/auth/forgot-password")
@api_route
def auth_forgot_password():
    data = cuerpo()
    correo = texto(data.get("correo")).lower()
    if not correo_valido(correo):
        raise ApiError("El correo no tiene un formato válido.", 400, "DATOS_INVALIDOS", {"correo": "formato"})
    usuario = read_one("usuarios", correo=correo)
    if usuario and usuario.get("activo") is not False:
        token, payload = crear_token(usuario, "reset", RESET_TTL)
        meta_guardar(
            "PASSWORD_RESET",
            payload.get("jti"),
            {"usuario_id": usuario.get("id"), "exp": payload.get("exp"), "usado": False},
            usuario_id=usuario.get("id"),
        )
        _, persona, nombre = perfil_persona(usuario)
        _enviar_correo_reset(correo, token, nombre)
    # Siempre responde ok para no revelar si el correo está registrado.
    return jsonify({"ok": True})


@bp.post("/auth/reset-password")
@api_route
def auth_reset_password():
    data = cuerpo()
    token = requerir(data, "token")
    nueva = texto(data.get("password_nueva"))
    confirmar = texto(data.get("confirmar"))
    if not _password_valida(nueva, exigir_simbolo=False):
        raise ApiError("La contraseña debe tener al menos 8 caracteres.", 400, "DATOS_INVALIDOS", {"password_nueva": "longitud"})
    if nueva != confirmar:
        raise ApiError("Las contraseñas no coinciden.", 400, "DATOS_INVALIDOS", {"confirmar": "no_coincide"})
    payload = decodificar_token(token, "reset")
    if not payload:
        raise ApiError("El token de recuperación es inválido o venció.", 400, "TOKEN_INVALIDO")
    registro = meta_obtener("PASSWORD_RESET", payload.get("jti")) or {}
    if registro.get("usado"):
        raise ApiError("El token de recuperación ya fue utilizado.", 400, "TOKEN_USADO")
    usuario = read_one("usuarios", id=payload.get("sub"))
    if not usuario:
        raise ApiError("El usuario no existe.", 404, "NO_ENCONTRADO")
    from app.api.common import update_row

    update_row("usuarios", {"password": hash_password(nueva)}, id=usuario.get("id"))
    meta_guardar(
        "PASSWORD_RESET",
        payload.get("jti"),
        {**registro, "usado": True, "usado_en": now_utc_iso()},
        usuario_id=usuario.get("id"),
    )
    return jsonify({"ok": True})


@bp.post("/auth/register")
@api_route
def auth_register():
    data = cuerpo()
    nombres = requerir(data, "nombres")
    apellidos = requerir(data, "apellidos")
    tipo_documento = requerir(data, "tipo_documento").upper()
    numero_documento = requerir(data, "numero_documento")
    telefono = requerir(data, "telefono")
    usuario = requerir(data, "usuario")
    correo = requerir(data, "correo").lower()
    password = texto(data.get("password"))
    confirmar = texto(data.get("confirmar_password"))

    if not 2 <= len(nombres) <= 60:
        raise ApiError("Los nombres deben tener entre 2 y 60 caracteres.", 400, "DATOS_INVALIDOS", {"nombres": "longitud"})
    if not 2 <= len(apellidos) <= 60:
        raise ApiError("Los apellidos deben tener entre 2 y 60 caracteres.", 400, "DATOS_INVALIDOS", {"apellidos": "longitud"})
    if tipo_documento not in {"DNI", "CE", "PASAPORTE", "RUC"}:
        raise ApiError("El tipo de documento no es válido.", 400, "DATOS_INVALIDOS", {"tipo_documento": "opciones"})
    if not numero_documento.isdigit():
        raise ApiError("El número de documento debe ser numérico.", 400, "DATOS_INVALIDOS", {"numero_documento": "numero"})
    if tipo_documento == "DNI" and len(numero_documento) != 8:
        raise ApiError("El DNI debe tener 8 dígitos.", 400, "DATOS_INVALIDOS", {"numero_documento": "longitud"})
    if tipo_documento == "RUC" and len(numero_documento) != 11:
        raise ApiError("El RUC debe tener 11 dígitos.", 400, "DATOS_INVALIDOS", {"numero_documento": "longitud"})
    if not telefono.isdigit() or len(telefono) != 9:
        raise ApiError("El teléfono debe tener 9 dígitos.", 400, "DATOS_INVALIDOS", {"telefono": "longitud"})
    if not 4 <= len(usuario) <= 30:
        raise ApiError("El usuario debe tener entre 4 y 30 caracteres.", 400, "DATOS_INVALIDOS", {"usuario": "longitud"})
    if not correo_valido(correo):
        raise ApiError("El correo no tiene un formato válido.", 400, "DATOS_INVALIDOS", {"correo": "formato"})
    if not _password_valida(password):
        raise ApiError(
            "La contraseña debe tener al menos 8 caracteres, una mayúscula y un símbolo.",
            400,
            "DATOS_INVALIDOS",
            {"password": "debil"},
        )
    if password != confirmar:
        raise ApiError("Las contraseñas no coinciden.", 400, "DATOS_INVALIDOS", {"confirmar_password": "no_coincide"})
    if read_one("usuarios", nombre_usuario=usuario):
        raise ApiError("El nombre de usuario ya está en uso.", 409, "USUARIO_OCUPADO", {"usuario": "ocupado"})
    if read_one("usuarios", correo=correo):
        raise ApiError("El correo ya está registrado.", 409, "CORREO_OCUPADO", {"correo": "ocupado"})

    from app.api.common import tipo_documento_id

    try:
        fila = rest_auth_service.register(
            nombre_usuario=usuario,
            correo=correo,
            password=password,
            rol="CLIENTE",
            nombres=nombres,
            apellidos=apellidos,
            tipo_documento_id=tipo_documento_id(tipo_documento),
            numero_documento=numero_documento,
            telefono=telefono,
        )
    except Exception as exc:  # noqa: BLE001
        mensaje = getattr(exc, "message", None) or str(exc) or "No se pudo crear la cuenta."
        raise ApiError(mensaje, 400, "DATOS_INVALIDOS") from exc

    return (
        jsonify(
            {
                "usuario_id": fila.get("id"),
                "cliente_id": fila.get("cliente_id"),
                "rol": "CLIENTE",
            }
        ),
        201,
    )


@bp.get("/auth/availability")
@api_route
def auth_availability():
    campo = texto(request.args.get("campo")).lower()
    valor = texto(request.args.get("valor"))
    if not campo or not valor:
        raise ApiError("Debes indicar campo y valor.", 400, "DATOS_INVALIDOS")
    disponible = True
    if campo == "usuario":
        disponible = read_one("usuarios", nombre_usuario=valor) is None
    elif campo == "correo":
        disponible = read_one("usuarios", correo=valor.lower()) is None
    elif campo == "documento":
        encontrado = read_one("clientes", numero_documento=valor)
        if not encontrado:
            encontrado = read_one("empleados", numero_documento=valor)
        disponible = encontrado is None
    else:
        raise ApiError("El campo debe ser usuario, correo o documento.", 400, "DATOS_INVALIDOS")
    return jsonify({"disponible": disponible})
