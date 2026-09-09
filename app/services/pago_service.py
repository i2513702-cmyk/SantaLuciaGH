"""Servicio de pagos: lectura de la venta/comprobante/pago para el resumen y PDF.

El registro de la venta + comprobante + pago simulado vive en carrito_service.pagar().
Aquí solo se LEEN datos para mostrar la confirmación y generar el comprobante PDF.
"""

from app.supabase_client import error_postgrest, get_reader


def crear_cliente(
    nombres: str,
    apellidos: str,
    tipo_documento_id: int,
    numero_documento: str,
    correo: str = "",
    telefono: str = "",
) -> int:
    """Crea (o reutiliza) un cliente en la tabla `clientes` por nº de documento."""
    numero = (numero_documento or "").strip()
    cliente = None
    if numero:
        try:
            resp = (
                get_reader()
                .table("clientes")
                .select("id")
                .eq("numero_documento", numero)
                .limit(1)
                .execute()
            )
            if resp.data:
                cliente = resp.data[0]
        except Exception:  # noqa: BLE001
            cliente = None

    if cliente:
        return int(cliente["id"])

    try:
        resp = get_reader().table("clientes").insert({
            "nombres": (nombres or "").strip(),
            "apellidos": (apellidos or "").strip(),
            "tipo_documento_id": int(tipo_documento_id or 1),
            "numero_documento": numero,
            "correo": (correo or "").strip() or None,
            "telefono": (telefono or "").strip() or None,
            "activo": True,
        }).execute()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(error_postgrest(exc)) from exc
    return int(resp.data[0]["id"])


def obtener_venta(venta_id: int) -> dict | None:
    try:
        resp = (
            get_reader()
            .table("ventas")
            .select("id,usuario_id,cliente_id,estado")
            .eq("id", venta_id)
            .limit(1)
            .execute()
        )
    except Exception:  # noqa: BLE001
        return None
    return resp.data[0] if resp.data else None


def _fmt(valor) -> float:
    try:
        return round(float(valor or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def resumen_venta(venta_id: int) -> dict | None:
    """Resumen completo de una venta para la confirmación y el PDF."""
    try:
        venta = (
            get_reader()
            .table("ventas")
            .select("*")
            .eq("id", venta_id)
            .limit(1)
            .execute()
        ).data
    except Exception:  # noqa: BLE001
        venta = []
    if not venta:
        return None
    venta = venta[0]

    def _uno(tabla, filtro):
        try:
            resp = (
                get_reader()
                .table(tabla)
                .select("*")
                .eq(filtro[0], filtro[1])
                .limit(1)
                .execute()
            )
            return resp.data[0] if resp.data else {}
        except Exception:  # noqa: BLE001
            return {}

    comprobante = _uno("comprobantes", ("venta_id", venta_id))
    pago = _uno("pagos", ("venta_id", venta_id))
    metodo = _uno("metodos_pago", ("id", pago.get("metodo_pago_id"))) if pago else {}

    try:
        items = (
            get_reader()
            .table("detalle_venta")
            .select(
                "id,producto_id,cantidad,precio_unitario,subtotal,"
                "productos(id,nombre)"
            )
            .eq("venta_id", venta_id)
            .order("id")
            .execute()
        ).data or []
    except Exception:  # noqa: BLE001
        items = []

    items_out = []
    for r in items:
        prod = r.get("productos") or {}
        pu = _fmt(r.get("precio_unitario"))
        st = _fmt(r.get("subtotal"))
        items_out.append({
            "producto_id": r.get("producto_id"),
            "nombre": prod.get("nombre") or "Producto",
            "cantidad": int(r.get("cantidad") or 0),
            "precio_unitario": f"S/ {pu:,.2f}",
            "precio_num": pu,
            "subtotal": f"S/ {st:,.2f}",
            "subtotal_num": st,
        })

    cliente_nombre = "Cliente anónimo (mostrador)"
    cliente_documento = "—"
    cliente_tipo = ""
    cliente = _uno("clientes", ("id", venta.get("cliente_id"))) if venta.get("cliente_id") else {}
    if cliente:
        nombre = " ".join(
            part for part in (cliente.get("nombres"), cliente.get("apellidos")) if part
        )
        cliente_nombre = nombre.strip() or "Cliente"
        cliente_documento = cliente.get("numero_documento") or "—"
        cliente_tipo = str(cliente.get("tipo_documento_id") or "")

    usuario_nombre = ""
    if venta.get("usuario_id"):
        u = _uno("usuarios", ("id", venta["usuario_id"]))
        usuario_nombre = u.get("nombre_usuario") or ""

    subtotal = _fmt(venta.get("subtotal"))
    igv = _fmt(venta.get("total")) - _fmt(venta.get("subtotal"))
    igv = round(igv, 2)
    total = _fmt(venta.get("total"))

    return {
        "venta_id": venta_id,
        "codigo_pedido": venta.get("codigo_pedido"),
        "estado": venta.get("estado"),
        "fecha_venta": venta.get("fecha_venta"),
        "usuario_nombre": usuario_nombre,
        "cliente_id": venta.get("cliente_id"),
        "cliente_nombre": cliente_nombre,
        "cliente_documento": cliente_documento,
        "cliente_tipo_documento_id": cliente_tipo,
        "tipo_comprobante": comprobante.get("tipo_comprobante") or "BOLETA",
        "serie": comprobante.get("serie") or "B001",
        "numero": int(comprobante.get("numero") or 0),
        "fecha_emision": comprobante.get("fecha_emision"),
        "subtotal": subtotal,
        "igv": igv,
        "total": total,
        "metodo_pago": metodo.get("nombre") or "—",
        "transaction_id": pago.get("transaction_id") or "",
        "estado_pago": pago.get("estado") or "",
        "items": items_out,
    }