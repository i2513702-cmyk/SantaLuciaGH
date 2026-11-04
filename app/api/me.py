"""Endpoints de la cuenta del cliente E34–E39."""

from __future__ import annotations

from flask import jsonify, request

from app.api import bp
from app.api.common import (
    ApiError,
    TIPOS_INTERNOS,
    api_auth,
    api_route,
    appointments_payload,
    cliente_de_usuario,
    contexto_actual,
    cuerpo,
    datetime_iso,
    entero,
    fecha_iso,
    paginacion,
    read_one,
    read_rows,
    texto,
    update_row,
    usuario_actual,
)
from app.services import rest_auth_service


def _sin_facturacion(pedido: dict) -> dict:
    pedido.pop("_facturacion", None)
    return pedido


@bp.get("/me/profile")
@api_route
@api_auth("CLIENTE")
def me_profile():
    usuario = usuario_actual()
    ctx = contexto_actual()
    cliente = cliente_de_usuario(usuario) or {}
    tipo = rest_auth_service.nombre_tipo_documento(cliente.get("tipo_documento_id"))
    return jsonify(
        {
            "usuario": {
                "id": usuario.get("id"),
                "usuario": usuario.get("nombre_usuario"),
                "correo": usuario.get("correo"),
                "rol": ctx.get("rol"),
                "activo": bool(usuario.get("activo")),
            },
            "cliente": {
                "tipo_documento": tipo,
                "numero_documento": cliente.get("numero_documento") or "",
                "nombres": cliente.get("nombres") or "",
                "apellidos": cliente.get("apellidos") or "",
                "telefono": cliente.get("telefono") or "",
                "correo": cliente.get("correo") or usuario.get("correo") or "",
                "verificado": bool(cliente.get("activo", True)),
                "miembro_desde": fecha_iso(cliente.get("fecha_registro") or usuario.get("fecha_creacion")),
            },
        }
    )


@bp.patch("/me/password")
@api_route
@api_auth()
def me_password():
    data = cuerpo()
    actual = texto(data.get("password_actual"))
    nueva = texto(data.get("password_nueva"))
    confirmar = texto(data.get("confirmar"))
    if not actual:
        raise ApiError("La contraseña actual es obligatoria.", 400, "DATOS_INVALIDOS", {"password_actual": "obligatorio"})
    if len(nueva) < 8 or not any(c.isupper() for c in nueva) or not any(not c.isalnum() for c in nueva):
        raise ApiError(
            "La nueva contraseña debe tener al menos 8 caracteres, una mayúscula y un símbolo.",
            400,
            "DATOS_INVALIDOS",
            {"password_nueva": "debil"},
        )
    if nueva != confirmar:
        raise ApiError("Las contraseñas no coinciden.", 400, "DATOS_INVALIDOS", {"confirmar": "no_coincide"})
    try:
        rest_auth_service.cambiar_clave(usuario_actual().get("nombre_usuario"), actual, nueva)
    except Exception as exc:  # noqa: BLE001
        mensaje = getattr(exc, "message", None) or str(exc) or "No se pudo cambiar la contraseña."
        raise ApiError(mensaje, 400, "DATOS_INVALIDOS") from exc
    return jsonify({"ok": True})


@bp.get("/me/orders")
@api_route
@api_auth("CLIENTE")
def me_orders():
    from app.api.orders import _serialize_order

    ctx = contexto_actual()
    page, limit, _offset = paginacion(10, 50)
    filas = read_rows("ventas", "id", order="id", desc=True)
    propias = []
    for fila in filas:
        venta = read_one("ventas", "*", id=fila.get("id"))
        if not venta:
            continue
        if venta.get("cliente_id") == ctx.get("cliente_id") or venta.get("usuario_id") == ctx.get("id"):
            propias.append(venta)
    total = len(propias)
    pagina = propias[(page - 1) * limit : page * limit]
    items = [_sin_facturacion(_serialize_order(int(v["id"]))) for v in pagina]
    return jsonify({"items": items, "page": page, "limit": limit, "total": total})


@bp.get("/me/appointments")
@api_route
@api_auth("CLIENTE")
def me_appointments():
    ctx = contexto_actual()
    page, limit, _offset = paginacion(10, 50)
    estado = texto(request.args.get("estado")).upper()
    fecha = texto(request.args.get("fecha"))
    reservas = read_rows("reservas", "*", cliente_id=ctx.get("cliente_id"), order="fecha_reserva", desc=True)
    if estado:
        reservas = [r for r in reservas if (r.get("estado") or "").upper() == estado]
    if fecha:
        reservas = [r for r in reservas if fecha_iso(r.get("fecha_reserva")) == fecha]
    total = len(reservas)
    pagina = reservas[(page - 1) * limit : page * limit]
    items = appointments_payload(pagina)
    for item in items:
        item.pop("_meta", None)
    return jsonify({"items": items, "page": page, "limit": limit, "total": total})


@bp.get("/me/notifications")
@api_route
@api_auth()
def me_notifications():
    ctx = contexto_actual()
    page, limit, _offset = paginacion(20, 50)
    filas = read_rows(
        "notificaciones",
        "*",
        usuario_id=ctx.get("id"),
        order="id",
        desc=True,
    )
    visibles = [f for f in filas if (f.get("tipo") or "").upper() not in TIPOS_INTERNOS]
    total = len(visibles)
    no_leidas = sum(1 for f in visibles if not f.get("leida"))
    pagina = visibles[(page - 1) * limit : page * limit]
    items = []
    for f in pagina:
        referencia = f.get("referencia") or ""
        items.append(
            {
                "id": f.get("id"),
                "titulo": f.get("titulo") or "",
                "mensaje": f.get("mensaje") or "",
                "tipo": f.get("tipo") or "",
                "leida": bool(f.get("leida")),
                "fecha": datetime_iso(f.get("fecha_creacion")),
                "enlace": referencia if referencia.startswith("/") else None,
            }
        )
    return jsonify({"items": items, "no_leidas": no_leidas, "page": page, "limit": limit, "total": total})


@bp.patch("/me/notifications/<int:notificacion_id>/read")
@api_route
@api_auth()
def me_notification_read(notificacion_id: int):
    ctx = contexto_actual()
    fila = read_one("notificaciones", "*", id=notificacion_id, usuario_id=ctx.get("id"))
    if not fila:
        raise ApiError("La notificación no existe.", 404, "NO_ENCONTRADO")
    update_row(
        "notificaciones",
        {"leida": True, "fecha_lectura": now_utc_iso()},
        id=notificacion_id,
    )
    return "", 204


def datetime_iso_now() -> str:
    from app.api.common import now_utc_iso

    return now_utc_iso()
