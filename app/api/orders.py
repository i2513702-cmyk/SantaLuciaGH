"""Endpoints de pedidos, pagos y ventas E28–E33."""

from __future__ import annotations

import hashlib
import hmac
import json
import os

from flask import Response, jsonify, request

from app.api import bp
from app.api.common import (
    ApiError,
    api_auth,
    api_route,
    codigo_pedido_siguiente,
    contexto_actual,
    cuerpo,
    datetime_iso,
    dinero,
    entero,
    fecha_iso,
    fecha_mas_dias,
    insert_row,
    meta_guardar,
    meta_obtener,
    mover_stock,
    now_utc_iso,
    read_one,
    read_rows,
    requerir,
    texto,
    update_row,
)
from app.config import Config
from app.services import carrito_service, pago_service, pdf_service


COMPROBANTES = ["BOLETA", "FACTURA"]
ENTREGAS = ["RECOJO", "DOMICILIO"]
IGV = 0.18


def _codigo_metodo(valor: str) -> str:
    valor = texto(valor).upper()
    mapa = {
        "CARD": "TARJETA",
        "VISA": "TARJETA",
        "MASTERCARD": "TARJETA",
        "TARJETA": "TARJETA",
        "YAPE": "YAPE",
        "PLIN": "PLIN",
        "SIMULADO": "SIMULADO",
        "EFECTIVO": "EFECTIVO",
        "CASH": "EFECTIVO",
    }
    return mapa.get(valor, valor)


def _metodo_pago(valor: str) -> dict:
    buscado = _codigo_metodo(valor)
    metodos = read_rows("metodos_pago", "*", order="id")
    for m in metodos:
        if _codigo_metodo(m.get("codigo") or m.get("nombre")) == buscado:
            return m
    # Algunos despliegues usan nombres comerciales (Visa, Yape, etc.).
    for m in metodos:
        nombre = texto(m.get("nombre")).upper()
        if buscado in nombre or nombre in buscado:
            return m
    if not buscado:
        raise ApiError("El método de pago es obligatorio.", 400, "DATOS_INVALIDOS", {"metodo_pago": "obligatorio"})
    # Crear el método si la tabla aún no lo tiene (p. ej. EFECTIVO en tienda).
    fila = insert_row(
        "metodos_pago",
        {
            "codigo": buscado,
            "nombre": buscado.title(),
            "activo": True,
            "es_online": buscado not in {"EFECTIVO"},
            "requiere_pasarela": buscado in {"TARJETA", "YAPE", "PLIN"},
        },
    )
    return fila


def _cliente_comprador(cliente: dict | None) -> dict:
    c = cliente or {}
    return {
        "nombre": " ".join(x for x in (texto(c.get("nombres")), texto(c.get("apellidos"))) if x).strip()
        or texto(c.get("razon_social"))
        or "",
        "documento": c.get("numero_documento") or "",
        "telefono": c.get("telefono") or "",
        "correo": c.get("correo") or "",
    }


def _items_venta(venta_id: int) -> list[dict]:
    filas = read_rows(
        "detalle_venta",
        "id,producto_id,cantidad,precio_unitario,subtotal,"
        "productos(codigo,nombre,marcas(nombre))",
        venta_id=venta_id,
        order="id",
    )
    if not filas:
        filas = read_rows("detalle_venta", "*", venta_id=venta_id, order="id")
    salida = []
    for f in filas:
        prod = f.get("productos") or {}
        cantidad = int(f.get("cantidad") or 0)
        base = dinero(f.get("subtotal") if f.get("subtotal") is not None else dinero(f.get("precio_unitario")) * cantidad)
        salida.append(
            {
                "producto_id": f.get("producto_id"),
                "sku": prod.get("codigo") or f"PRO-{f.get('producto_id')}",
                "nombre": prod.get("nombre") or "",
                "marca": (prod.get("marcas") or {}).get("nombre") or "",
                "cantidad": cantidad,
                "precio_unit": dinero(f.get("precio_unitario")),
                "subtotal_base": base,
                "igv": round(base * IGV, 2),
                "envio": 0.0,
                "total": round(base * (1 + IGV), 2),
            }
        )
    return salida


def _observaciones(venta: dict) -> dict:
    valor = venta.get("observaciones")
    if isinstance(valor, dict):
        return valor
    try:
        datos = json.loads(valor or "{}")
        return datos if isinstance(datos, dict) else {}
    except (TypeError, ValueError):
        return {}


def _serialize_order(venta_id: int) -> dict:
    venta = read_one("ventas", "*", id=venta_id)
    if not venta:
        raise ApiError("El pedido no existe.", 404, "NO_ENCONTRADO")
    comprobante = read_one("comprobantes", "*", venta_id=venta_id)
    pago = read_one("pagos", "*", venta_id=venta_id, order_by="id") if False else None
    if pago is None:
        filas_pago = read_rows("pagos", "*", venta_id=venta_id, order="id", desc=True, limit=1)
        pago = filas_pago[0] if filas_pago else {}
    metodo = read_one("metodos_pago", "*", id=pago.get("metodo_pago_id")) if pago.get("metodo_pago_id") else {}
    obs = _observaciones(venta)
    items = _items_venta(venta_id)
    envio = dinero(venta.get("costo_envio"))
    total = dinero(venta.get("total"))
    estado = venta.get("estado") or "PENDIENTE_PAGO"
    pagado = (pago.get("estado") or "").upper() == "CONFIRMADO" or estado == "PAGO_CONFIRMADO"
    entrega_tipo = venta.get("tipo_entrega")
    if entrega_tipo == "RECOJO_TIENDA":
        entrega_tipo = "RECOJO"
    if entrega_tipo not in {"RECOJO", "DOMICILIO"}:
        entrega_tipo = "RECOJO"
    seguimiento = [
        {
            "etapa": "PEDIDO_CONFIRMADO",
            "estado": "COMPLETADO",
            "fecha": datetime_iso(venta.get("fecha_venta")),
        },
        {
            "etapa": "PAGO_CONFIRMADO",
            "estado": "COMPLETADO" if pagado else "PENDIENTE",
            "fecha": datetime_iso(pago.get("fecha_pago")) if pagado else None,
        },
        {
            "etapa": "EN_PREPARACION",
            "estado": "COMPLETADO" if pagado and estado in {"PAGO_CONFIRMADO", "ENVIADO", "ENTREGADO"} else "PENDIENTE",
            "fecha": None,
        },
        {
            "etapa": "ENTREGADO",
            "estado": "COMPLETADO" if estado == "ENTREGADO" else "PENDIENTE",
            "fecha": None,
        },
    ]
    return {
        "id": venta.get("id"),
        "codigo": venta.get("codigo_pedido"),
        "estado": estado,
        "fecha": datetime_iso(venta.get("fecha_venta") or venta.get("fecha_creacion")),
        "items": items,
        "envio": envio,
        "total": total,
        "comprobante": {
            "tipo": comprobante.get("tipo_comprobante") or "BOLETA",
            "serie_numero": (
                f"{comprobante.get('serie')}-{int(comprobante.get('numero') or 0):08d}"
                if comprobante
                else ""
            ),
            "pdf_url": f"/api/v1/orders/{venta_id}/receipt.pdf",
        },
        "pago": {
            "metodo": metodo.get("nombre") or pago.get("pasarela") or "",
            "estado": pago.get("estado") or "PENDIENTE",
            "referencia": pago.get("transaction_id") or "",
        },
        "entrega": {
            "tipo": entrega_tipo,
            "direccion": obs.get("direccion_entrega") or "",
            "estado": "ENTREGADO" if estado == "ENTREGADO" else "PENDIENTE",
        },
        "seguimiento": seguimiento,
        "garantia_hasta": fecha_mas_dias(venta.get("fecha_venta"), 90),
    }


def _puede_ver_venta(venta: dict) -> bool:
    ctx = contexto_actual()
    rol = ctx.get("rol")
    if rol in {"ADMINISTRADOR", "SUPERVISOR", "VENDEDOR"}:
        return True
    if rol == "CLIENTE":
        return (
            venta.get("cliente_id") == ctx.get("cliente_id")
            or venta.get("usuario_id") == ctx.get("id")
        )
    return False


def _metodos_publicos() -> list[dict]:
    filas = read_rows("metodos_pago", "codigo,nombre", order="id")
    return [{"codigo": m.get("codigo"), "nombre": m.get("nombre")} for m in filas]


@bp.get("/orders/summary")
@api_route
@api_auth("CLIENTE")
def orders_summary():
    from app.api.cart import _payload
    from app.services import carrito_service

    carrito_id, _nuevo, _expirado = carrito_service.obtener_carrito_vigente(f"api:{contexto_actual().get('id')}")
    carrito = _payload(carrito_id)
    from app.api.common import cliente_de_usuario

    cliente = cliente_de_usuario(contexto_actual().get("usuario") or {})
    items = [
        {
            "producto_id": i.get("producto_id"),
            "nombre": i.get("nombre"),
            "cantidad": i.get("cantidad"),
            "precio_unit": i.get("precio_unit"),
            "subtotal": i.get("subtotal"),
            "subtotal_base": i.get("subtotal_base"),
            "igv": i.get("igv"),
            "total": i.get("total"),
        }
        for i in carrito.get("items", [])
    ]
    return jsonify(
        {
            "items": items,
            "comprador": _cliente_comprador(cliente),
            "comprobantes": COMPROBANTES,
            "metodos_pago": _metodos_publicos(),
            "tipos_entrega": ENTREGAS,
            "subtotal_base": carrito.get("subtotal_base"),
            "igv": carrito.get("igv"),
            "total": carrito.get("total"),
        }
    )


@bp.post("/orders")
@api_route
@api_auth("CLIENTE")
def orders_create():
    data = cuerpo()
    clave = texto(request.headers.get("Idempotency-Key"))
    if not clave:
        raise ApiError("La cabecera Idempotency-Key es obligatoria.", 400, "DATOS_INVALIDOS")
    existente = meta_obtener("IDEMPOTENCIA", clave)
    if isinstance(existente, dict) and existente.get("venta_id"):
        venta = read_one("ventas", "id", id=entero(existente.get("venta_id")))
        if venta:
            return jsonify(_serialize_order(int(venta["id"])))

    tipo_comprobante = texto(data.get("tipo_comprobante")).upper()
    if tipo_comprobante not in COMPROBANTES:
        raise ApiError("El comprobante debe ser BOLETA o FACTURA.", 400, "DATOS_INVALIDOS", {"tipo_comprobante": "opciones"})
    facturacion = data.get("facturacion") if isinstance(data.get("facturacion"), dict) else {}
    if tipo_comprobante == "FACTURA":
        ruc = texto(facturacion.get("ruc"))
        if len(ruc) != 11 or not ruc.isdigit():
            raise ApiError("El RUC debe tener 11 dígitos.", 400, "DATOS_INVALIDOS", {"facturacion.ruc": "longitud"})
        if not texto(facturacion.get("razon_social")):
            raise ApiError("La razón social es obligatoria.", 400, "DATOS_INVALIDOS", {"facturacion.razon_social": "obligatorio"})
        if not texto(facturacion.get("direccion_fiscal")):
            raise ApiError("La dirección fiscal es obligatoria.", 400, "DATOS_INVALIDOS", {"facturacion.direccion_fiscal": "obligatorio"})

    tipo_entrega = texto(data.get("tipo_entrega")).upper()
    if tipo_entrega not in ENTREGAS:
        raise ApiError("El tipo de entrega debe ser RECOJO o DOMICILIO.", 400, "DATOS_INVALIDOS", {"tipo_entrega": "opciones"})
    direccion_entrega = texto(data.get("direccion_entrega"))
    if tipo_entrega == "DOMICILIO" and not direccion_entrega:
        raise ApiError("La dirección de entrega es obligatoria.", 400, "DATOS_INVALIDOS", {"direccion_entrega": "obligatorio"})
    metodo_texto = texto(data.get("metodo_pago")).upper()
    if metodo_texto not in {"TARJETA", "YAPE", "PLIN", "SIMULADO"}:
        raise ApiError("El método de pago no es válido para la web.", 400, "DATOS_INVALIDOS", {"metodo_pago": "opciones"})
    metodo = _metodo_pago(metodo_texto)
    token_pago = texto(data.get("token_pago"))

    ctx = contexto_actual()
    cliente_id = ctx.get("cliente_id")
    if not cliente_id:
        raise ApiError("Tu cuenta no tiene un cliente asociado.", 400, "DATOS_INVALIDOS")
    carrito_id, _nuevo, _expirado = carrito_service.obtener_carrito_vigente(f"api:{ctx.get('id')}")
    try:
        resultado = carrito_service.pagar(
            carrito_id,
            int(metodo.get("id")),
            usuario_id=ctx.get("id"),
            tipo_comprobante=tipo_comprobante,
            cliente_id=cliente_id,
        )
    except Exception as exc:  # noqa: BLE001
        mensaje = getattr(exc, "message", None) or str(exc) or "No se pudo registrar el pedido."
        raise ApiError(mensaje, 409, "NO_SE_PUDO_PAGAR") from exc
    venta_id = int(resultado["venta_id"])
    obs = {
        "facturacion": facturacion,
        "direccion_entrega": direccion_entrega,
        "tipo_entrega": tipo_entrega,
        "metodo_pago": metodo_texto,
    }
    try:
        update_row(
            "ventas",
            {
                "tipo_entrega": ("RECOJO_TIENDA" if tipo_entrega == "RECOJO" else tipo_entrega),
                "observaciones": json.dumps(obs, ensure_ascii=False),
                "costo_envio": 0.0,
            },
            id=venta_id,
        )
    except ApiError:
        pass
    estado_pago = "CONFIRMADO" if metodo_texto == "SIMULADO" or token_pago else "PENDIENTE"
    try:
        pagos = read_rows("pagos", "id", venta_id=venta_id, order="id", desc=True, limit=1)
        if pagos:
            update_row(
                "pagos",
                {
                    "estado": estado_pago,
                    "canal_pago": "ONLINE",
                    "transaction_id": token_pago or resultado.get("codigo_pedido") or f"ORD-{venta_id}",
                    "order_id": resultado.get("codigo_pedido") or f"ORD-{venta_id}",
                    "fecha_pago": now_utc_iso() if estado_pago == "CONFIRMADO" else None,
                },
                id=pagos[0]["id"],
            )
    except ApiError:
        pass
    meta_guardar("IDEMPOTENCIA", clave, {"venta_id": venta_id, "fecha": now_utc_iso()}, usuario_id=ctx.get("id"))
    return jsonify(_serialize_order(venta_id)), 201


@bp.post("/orders/webhook")
@api_route
def orders_webhook():
    secreto = (getattr(Config, "PAYMENT_WEBHOOK_SECRET", "") or os.getenv("PAYMENT_WEBHOOK_SECRET") or os.getenv("SUPABASE_WEBHOOK_SECRET") or "").strip()
    if not secreto:
        raise ApiError("El webhook no está configurado.", 401, "NO_CONFIGURADO")
    firma = texto(request.headers.get("X-Signature"))
    if firma.lower().startswith("sha256="):
        firma = firma[7:]
    esperada = hmac.new(secreto.encode("utf-8"), request.get_data(), hashlib.sha256).hexdigest()
    if not firma or not hmac.compare_digest(esperada, firma):
        raise ApiError("La firma del webhook no es válida.", 401, "FIRMA_INVALIDA")
    data = cuerpo()
    pago_id = texto(data.get("pago_id"))
    estado = texto(data.get("estado")).upper()
    referencia = texto(data.get("referencia"))
    if estado not in {"APROBADO", "RECHAZADO"}:
        raise ApiError("El estado debe ser APROBADO o RECHAZADO.", 400, "DATOS_INVALIDOS")
    pago = read_one("pagos", "*", id=entero(pago_id))
    if not pago:
        pago = read_one("pagos", "*", transaction_id=pago_id)
    if not pago and referencia:
        pago = read_one("pagos", "*", order_id=referencia)
    if not pago:
        raise ApiError("El pago no existe.", 404, "NO_ENCONTRADO")
    estado_db = "CONFIRMADO" if estado == "APROBADO" else "PENDIENTE"
    update_row(
        "pagos",
        {
            "estado": estado_db,
            "transaction_id": referencia or pago.get("transaction_id"),
            "fecha_pago": now_utc_iso(),
            "response_code": estado,
        },
        id=pago.get("id"),
    )
    if pago.get("venta_id"):
        update_row(
            "ventas",
            {"estado": "PAGO_CONFIRMADO" if estado == "APROBADO" else "PAGO_RECHAZADO"},
            id=pago.get("venta_id"),
        )
    return jsonify({"ok": True})


@bp.get("/orders/<int:venta_id>")
@api_route
@api_auth("CLIENTE", "VENDEDOR", "SUPERVISOR", "ADMINISTRADOR")
def orders_get(venta_id: int):
    venta = read_one("ventas", "*", id=venta_id)
    if not venta:
        raise ApiError("El pedido no existe.", 404, "NO_ENCONTRADO")
    if not _puede_ver_venta(venta):
        raise ApiError("No tienes permiso para ver este pedido.", 403, "SIN_PERMISO")
    return jsonify(_serialize_order(venta_id))


@bp.get("/orders/<int:venta_id>/receipt.pdf")
@api_route
@api_auth("CLIENTE", "VENDEDOR", "SUPERVISOR", "ADMINISTRADOR")
def orders_receipt(venta_id: int):
    venta = read_one("ventas", "*", id=venta_id)
    if not venta:
        raise ApiError("El pedido no existe.", 404, "NO_ENCONTRADO")
    if not _puede_ver_venta(venta):
        raise ApiError("No tienes permiso para ver este pedido.", 403, "SIN_PERMISO")
    resumen = pago_service.resumen_venta(venta_id)
    if not resumen:
        raise ApiError("No se pudo preparar el comprobante.", 404, "NO_ENCONTRADO")
    pdf = pdf_service.generar_comprobante_pdf(resumen)
    serie = (resumen.get("serie") or "comprobante").replace("/", "-")
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={serie}.pdf"},
    )


def _crear_venta_tienda(
    cliente_id: int,
    items: list[dict],
    tipo_comprobante: str,
    facturacion: dict,
    metodo: dict,
    usuario_id: int,
) -> int:
    preparados = []
    for item in items:
        producto_id = entero(item.get("producto_id"))
        cantidad = entero(item.get("cantidad"))
        if producto_id <= 0 or cantidad <= 0:
            raise ApiError("Cada ítem necesita producto_id y cantidad válidos.", 400, "DATOS_INVALIDOS")
        producto = read_one("productos", "*", id=producto_id)
        if not producto or producto.get("activo") is False:
            raise ApiError(f"El producto {producto_id} no está disponible.", 400, "PRODUCTO_INVALIDO")
        precio = dinero(producto.get("precio_promocional") or producto.get("precio_venta"))
        preparados.append({"producto_id": producto_id, "cantidad": cantidad, "precio": precio, "nombre": producto.get("nombre")})
    if not preparados:
        raise ApiError("Agrega al menos un producto.", 400, "DATOS_INVALIDOS")
    subtotal = round(sum(p["precio"] * p["cantidad"] for p in preparados), 2)
    igv = round(subtotal * IGV, 2)
    total = round(subtotal + igv, 2)
    codigo = codigo_pedido_siguiente()
    venta = insert_row(
        "ventas",
        {
            "cliente_id": cliente_id,
            "usuario_id": usuario_id,
            "codigo_pedido": codigo,
            "canal_venta": "TIENDA",
            "tipo_entrega": "RECOJO_TIENDA",
            "fecha_venta": now_utc_iso(),
            "subtotal": subtotal,
            "descuento": 0.0,
            "costo_envio": 0.0,
            "total": total,
            "estado": "PAGO_CONFIRMADO",
            "observaciones": json.dumps({"facturacion": facturacion, "canal": "TIENDA"}, ensure_ascii=False),
        },
    )
    venta_id = int(venta["id"])
    try:
        filas = [
            {
                "venta_id": venta_id,
                "producto_id": p["producto_id"],
                "cantidad": p["cantidad"],
                "precio_unitario": p["precio"],
                "subtotal": round(p["precio"] * p["cantidad"], 2),
                "descuento": 0.0,
            }
            for p in preparados
        ]
        insert_row("detalle_venta", filas) if isinstance(filas, dict) else None
        if filas:
            from app.supabase_client import get_admin_client

            get_admin_client().table("detalle_venta").insert(filas).execute()
    except Exception as exc:  # noqa: BLE001
        raise ApiError(f"No se pudo registrar el detalle: {exc}", 500, "ERROR_ESCRITURA") from exc

    serie = "B001" if tipo_comprobante == "BOLETA" else "F001"
    ultimo = read_rows("comprobantes", "numero", serie=serie, order="numero", desc=True, limit=1)
    numero = (int(ultimo[0].get("numero") or 0) + 1) if ultimo else 1
    insert_row(
        "comprobantes",
        {
            "venta_id": venta_id,
            "tipo_comprobante": tipo_comprobante,
            "serie": serie,
            "numero": numero,
            "fecha_emision": now_utc_iso(),
            "subtotal": subtotal,
            "igv": igv,
            "total": total,
            "estado": "EMITIDO",
        },
    )
    insert_row(
        "pagos",
        {
            "venta_id": venta_id,
            "metodo_pago_id": metodo.get("id"),
            "usuario_id": usuario_id,
            "canal_pago": "ONLINE",
            "monto": total,
            "estado": "CONFIRMADO",
            "pasarela": metodo.get("codigo"),
            "transaction_id": codigo,
            "order_id": codigo,
            "fecha_pago": now_utc_iso(),
        },
    )
    for p in preparados:
        mover_stock(p["producto_id"], "SALIDA", p["cantidad"], f"Venta {codigo}", usuario_id)
    return venta_id


@bp.post("/sales")
@api_route
@api_auth("VENDEDOR", "ADMINISTRADOR")
def sales_create():
    data = cuerpo()
    cliente_id = entero(data.get("cliente_id"))
    documento = texto(data.get("documento"))
    if cliente_id <= 0 and documento:
        cliente = read_one("clientes", "*", numero_documento=documento)
        cliente_id = int(cliente["id"]) if cliente else 0
    if cliente_id <= 0:
        raise ApiError("Indica un cliente o su documento.", 400, "DATOS_INVALIDOS", {"cliente_id": "obligatorio"})
    if not read_one("clientes", "id", id=cliente_id):
        raise ApiError("El cliente no existe.", 404, "NO_ENCONTRADO")
    tipo_comprobante = texto(data.get("tipo_comprobante")).upper()
    if tipo_comprobante not in COMPROBANTES:
        raise ApiError("El comprobante debe ser BOLETA o FACTURA.", 400, "DATOS_INVALIDOS")
    facturacion = data.get("facturacion") if isinstance(data.get("facturacion"), dict) else {}
    if tipo_comprobante == "FACTURA":
        ruc = texto(facturacion.get("ruc"))
        if len(ruc) != 11 or not ruc.isdigit():
            raise ApiError("El RUC debe tener 11 dígitos.", 400, "DATOS_INVALIDOS", {"facturacion.ruc": "longitud"})
        if not texto(facturacion.get("razon_social")) or not texto(facturacion.get("direccion_fiscal")):
            raise ApiError("Completa los datos de facturación.", 400, "DATOS_INVALIDOS")
    metodo_texto = texto(data.get("metodo_pago")).upper()
    if metodo_texto not in {"EFECTIVO", "TARJETA", "YAPE", "PLIN"}:
        raise ApiError("El método de pago no es válido.", 400, "DATOS_INVALIDOS")
    metodo = _metodo_pago(metodo_texto)
    items = data.get("items") if isinstance(data.get("items"), list) else []
    venta_id = _crear_venta_tienda(cliente_id, items, tipo_comprobante, facturacion, metodo, contexto_actual().get("id"))
    pedido = _serialize_order(venta_id)
    pedido.pop("_facturacion", None)
    monto_recibido = data.get("monto_recibido")
    if monto_recibido not in (None, ""):
        recibido = dinero(monto_recibido)
        if recibido < dinero(pedido.get("total")):
            raise ApiError("El monto recibido es menor al total.", 400, "MONTO_INSUFICIENTE")
        pedido["vuelto"] = round(recibido - dinero(pedido.get("total")), 2)
    return jsonify(pedido), 201
