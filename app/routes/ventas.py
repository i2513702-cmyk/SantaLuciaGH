"""Ventas y ordenes: listado con filtros y detalle (admin, supervisor, vendedor, recepcionista)."""

from datetime import date, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.decorators import ventas_required
from app.services.kpi_service import LIMA, _desde_ts, _hasta_ts
from app.supabase_client import get_reader

bp = Blueprint("ventas", __name__)
POR_PAGINA = 20
CAMPOS = "id,codigo_pedido,fecha_venta,estado,canal_venta,tipo_entrega,total,cliente_id,usuario_id"


def _fecha(txt):
    try:
        return date.fromisoformat((txt or "").strip()[:10])
    except ValueError:
        return None


def _nombres(tabla, campos, ids):
    """{id: fila} sin joins embebidos (no dependen de las FK del esquema)."""
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    filas = get_reader().table(tabla).select(f"id,{campos}").in_("id", ids).execute().data or []
    return {f["id"]: f for f in filas}


def _cliente(c):
    if not c:
        return "Cliente web"
    return (f"{c.get('nombres') or ''} {c.get('apellidos') or ''}".strip()) or c.get("razon_social") or "Cliente"


@bp.get("/ventas")
@ventas_required
def listado():
    q = request.args.get("q", "").strip()[:40]
    estado = request.args.get("estado", "").strip()
    canal = request.args.get("canal", "").strip()
    desde, hasta = _fecha(request.args.get("desde")), _fecha(request.args.get("hasta"))
    pagina = max(request.args.get("p", 1, type=int), 1)

    def filtrar(b):
        if q:
            b = b.ilike("codigo_pedido", f"%{q}%")
        if estado:
            b = b.eq("estado", estado)
        if canal:
            b = b.eq("canal_venta", canal)
        if desde:
            b = b.gte("fecha_venta", _desde_ts(desde))
        if hasta:
            b = b.lt("fecha_venta", _hasta_ts(hasta))
        return b

    error, ventas, total_filas = None, [], 0
    resumen = {"monto": 0.0, "pedidos": 0, "ticket": 0.0}
    estados, canales = [], []
    try:
        r = filtrar(get_reader().table("ventas").select(CAMPOS, count="exact")).order(
            "fecha_venta", desc=True
        ).range((pagina - 1) * POR_PAGINA, pagina * POR_PAGINA - 1).execute()
        ventas, total_filas = r.data or [], r.count or 0
        clientes = _nombres("clientes", "nombres,apellidos,razon_social", [v.get("cliente_id") for v in ventas])
        usuarios = _nombres("usuarios", "nombre_usuario", [v.get("usuario_id") for v in ventas])
        for v in ventas:
            v["cliente"] = _cliente(clientes.get(v.get("cliente_id"))) if v.get("cliente_id") else (
                (usuarios.get(v.get("usuario_id")) or {}).get("nombre_usuario") or "Cliente web"
            )
        # Resumen de TODO el filtro (no solo la pagina), solo columna total.
        monto, inicio = 0.0, 0
        while True:
            lote = filtrar(get_reader().table("ventas").select("total")).order("id").range(inicio, inicio + 999).execute().data or []
            monto += sum(float(x.get("total") or 0) for x in lote)
            if len(lote) < 1000:
                break
            inicio += 1000
        resumen = {"monto": monto, "pedidos": total_filas, "ticket": monto / total_filas if total_filas else 0.0}
        for col, destino in (("estado", estados), ("canal_venta", canales)):
            fila = get_reader().table("ventas").select(col).limit(1000).execute().data or []
            destino.extend(sorted({f[col] for f in fila if f.get(col)}))
    except Exception as exc:  # noqa: BLE001
        error = f"No se pudieron cargar las ventas: {exc}"

    hoy = date.today()
    atajos = {
        "Hoy": (hoy, hoy),
        "7 días": (hoy - timedelta(days=6), hoy),
        "Este mes": (hoy.replace(day=1), hoy),
    }
    args = {k: v for k, v in {"q": q, "estado": estado, "canal": canal,
                              "desde": desde.isoformat() if desde else "",
                              "hasta": hasta.isoformat() if hasta else ""}.items() if v}
    return render_template(
        "ventas/listado.html", ventas=ventas, resumen=resumen, error=error, args=args,
        pagina=pagina, paginas=max((total_filas + POR_PAGINA - 1) // POR_PAGINA, 1),
        estados=estados, canales=canales, atajos=atajos, f=args,
    )


@bp.get("/ventas/<int:venta_id>")
@ventas_required
def detalle(venta_id):
    rd = get_reader()
    try:
        filas = rd.table("ventas").select("*").eq("id", venta_id).limit(1).execute().data
        if not filas:
            abort(404)
        venta = filas[0]
        items = rd.table("detalle_venta").select("*").eq("venta_id", venta_id).order("id").execute().data or []
        prods = _nombres("productos", "nombre", [i.get("producto_id") for i in items])
        for i in items:
            i["nombre"] = (prods.get(i.get("producto_id")) or {}).get("nombre") or f"Producto #{i.get('producto_id')}"
        pagos = rd.table("pagos").select("*").eq("venta_id", venta_id).execute().data or []
        cliente = _nombres("clientes", "nombres,apellidos,razon_social,telefono,correo", [venta.get("cliente_id")]).get(venta.get("cliente_id"))
        usuario = _nombres("usuarios", "nombre_usuario,correo", [venta.get("usuario_id")]).get(venta.get("usuario_id"))
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "code", None) == 404:
            raise
        abort(500, description=str(exc))
    return render_template("ventas/detalle.html", venta=venta, items=items, pagos=pagos,
                           cliente=cliente, usuario=usuario, nombre_cliente=_cliente(cliente) if cliente else None)


@bp.post("/ventas/<int:venta_id>/pago/aprobar")
@ventas_required
def aprobar_pago(venta_id):
    from app.supabase_client import get_admin_client, get_reader
    try:
        pagos = get_reader().table("pagos").select("id").eq("venta_id", venta_id).execute().data or []
        for pg in pagos:
            get_admin_client().table("pagos").update({"estado": "CONFIRMADO"}).eq("id", pg["id"]).execute()
        get_admin_client().table("ventas").update({"estado": "PAGADA"}).eq("id", venta_id).execute()
        flash("Pago aprobado.", "success")
    except Exception as exc:  # noqa: BLE001
        flash("No se pudo aprobar el pago.", "error")
    return redirect(url_for("ventas.detalle", venta_id=venta_id))


@bp.post("/ventas/<int:venta_id>/pago/rechazar")
@ventas_required
def rechazar_pago(venta_id):
    from app.supabase_client import get_admin_client, get_reader
    motivo = (request.form.get("motivo") or "").strip()
    try:
        pagos = get_reader().table("pagos").select("id").eq("venta_id", venta_id).execute().data or []
        for pg in pagos:
            get_admin_client().table("pagos").update({
                "estado": "RECHAZADO",
                "observaciones": motivo or "Rechazado por el administrador",
            }).eq("id", pg["id"]).execute()
        get_admin_client().table("ventas").update({"estado": "RECHAZADO"}).eq("id", venta_id).execute()
        flash("Pago rechazado.", "warning")
    except Exception as exc:  # noqa: BLE001
        flash("No se pudo rechazar el pago.", "error")
    return redirect(url_for("ventas.detalle", venta_id=venta_id))


# --------------------------------------------------------------------------
# Reservas de stock (pagos en efectivo pendientes de cobro)
#
# Al pagar en EFECTIVO el checkout NO descuenta stock: lo deja en `stock_actual`
# y suma la cantidad a `stock_reservado`. Al resolver la reserva:
#   - Confirmar pago -> stock_actual -= cant  y  stock_reservado -= cant
#   - Cancelar       -> stock_reservado -= cant  (el stock fisico no se toco)
# --------------------------------------------------------------------------
ESTADOS_VENTA_CERRADA = ("CANCELADA", "RECHAZADO", "PAGO_RECHAZADO")


def _ahora():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _destino_seguro(por_defecto):
    """Redirige a `next` solo si es una ruta interna (evita open redirect)."""
    nxt = (request.form.get("next") or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return por_defecto


def _ids_metodo_efectivo():
    filas = get_reader().table("metodos_pago").select("id").eq("codigo", "EFECTIVO").execute().data or []
    return [f["id"] for f in filas]


def _pagos_efectivo_pendientes(venta_id):
    """Pagos en efectivo sin cobrar de la venta: son los que mantienen stock reservado."""
    ids = _ids_metodo_efectivo()
    if not ids:
        return []
    return (
        get_reader().table("pagos").select("id").eq("venta_id", venta_id)
        .eq("estado", "PENDIENTE").in_("metodo_pago_id", ids).execute().data or []
    )


def _resolver_stock(venta_id, cobrado):
    """Libera la reserva de cada linea de la venta; si `cobrado`, tambien descuenta el stock."""
    from app.supabase_client import get_admin_client
    items = get_reader().table("detalle_venta").select("producto_id,cantidad").eq("venta_id", venta_id).execute().data or []
    for it in items:
        pid = it.get("producto_id")
        cant = float(it.get("cantidad") or 0)
        if not pid or cant <= 0:
            continue
        filas = get_reader().table("inventario").select("id,stock_actual,stock_reservado").eq("producto_id", pid).execute().data or []
        if not filas:
            continue
        f = filas[0]
        cambios = {
            "stock_reservado": max(float(f.get("stock_reservado") or 0) - cant, 0),
            "fecha_actualizacion": _ahora(),
        }
        if cobrado:
            cambios["stock_actual"] = max(float(f.get("stock_actual") or 0) - cant, 0)
        get_admin_client().table("inventario").update(cambios).eq("id", f["id"]).execute()


@bp.get("/ventas/reservas")
@ventas_required
def reservas():
    """Reservas pendientes (efectivo sin cobrar), opcionalmente de un solo producto."""
    producto_id = request.args.get("producto_id", type=int)
    filas, producto, error = [], None, None
    try:
        rd = get_reader()
        if producto_id:
            r = rd.table("productos").select("id,codigo,nombre").eq("id", producto_id).limit(1).execute().data
            producto = r[0] if r else None
        ids_metodo = _ids_metodo_efectivo()
        pagos = []
        if ids_metodo:
            pagos = (
                rd.table("pagos").select("id,venta_id,monto,fecha_creacion")
                .eq("estado", "PENDIENTE").in_("metodo_pago_id", ids_metodo).execute().data or []
            )
        venta_ids = sorted({p["venta_id"] for p in pagos if p.get("venta_id")})
        ventas = {}
        if venta_ids:
            for v in rd.table("ventas").select("id,codigo_pedido,fecha_venta,estado,total,cliente_id,usuario_id,fecha_limite_pago").in_("id", venta_ids).execute().data or []:
                if v.get("estado") not in ESTADOS_VENTA_CERRADA:
                    ventas[v["id"]] = v
        lineas = {}
        if ventas:
            for d in rd.table("detalle_venta").select("venta_id,producto_id,cantidad").in_("venta_id", list(ventas)).execute().data or []:
                lineas.setdefault(d["venta_id"], []).append(d)
        prods = _nombres("productos", "codigo,nombre", [d["producto_id"] for ls in lineas.values() for d in ls])
        clientes = _nombres("clientes", "nombres,apellidos,razon_social", [v.get("cliente_id") for v in ventas.values()])
        usuarios = _nombres("usuarios", "nombre_usuario", [v.get("usuario_id") for v in ventas.values()])
        for vid, v in sorted(ventas.items(), key=lambda kv: kv[1].get("fecha_venta") or "", reverse=True):
            ls = lineas.get(vid, [])
            if producto_id and not any(d["producto_id"] == producto_id for d in ls):
                continue
            filas.append({
                "venta": v,
                "cliente": _cliente(clientes.get(v.get("cliente_id"))) if v.get("cliente_id") else (
                    (usuarios.get(v.get("usuario_id")) or {}).get("nombre_usuario") or "Cliente web"),
                "items": [{
                    "nombre": (prods.get(d["producto_id"]) or {}).get("nombre") or f"Producto #{d['producto_id']}",
                    "cantidad": d.get("cantidad"),
                    "resaltar": d["producto_id"] == producto_id,
                } for d in ls],
            })
    except Exception as exc:  # noqa: BLE001
        error = f"No se pudieron cargar las reservas: {exc}"
    return render_template("ventas/reservas.html", filas=filas, producto=producto,
                           producto_id=producto_id, error=error)


@bp.post("/ventas/<int:venta_id>/marcar-pagado")
@ventas_required
def marcar_pagado(venta_id):
    """Confirma el cobro en efectivo: descuenta el stock y libera la reserva."""
    from app.supabase_client import get_admin_client
    destino = _destino_seguro(url_for("ventas.detalle", venta_id=venta_id))
    try:
        pendientes = _pagos_efectivo_pendientes(venta_id)
        if not pendientes:
            flash("Esta venta no tiene un pago en efectivo pendiente (ya fue confirmada o cancelada).", "warning")
            return redirect(destino)
        # Primero se marca el pago: actua de candado para que un doble clic no descuente dos veces.
        for pg in pendientes:
            get_admin_client().table("pagos").update(
                {"estado": "CONFIRMADO", "fecha_pago": _ahora()}).eq("id", pg["id"]).execute()
        _resolver_stock(venta_id, cobrado=True)
        get_admin_client().table("ventas").update({"estado": "PAGO_CONFIRMADO"}).eq("id", venta_id).execute()
        flash("Pago confirmado: stock descontado y reserva liberada.", "success")
    except Exception as exc:  # noqa: BLE001
        flash("No se pudo confirmar el pago. Revisa el stock del producto.", "error")
    return redirect(destino)


@bp.post("/ventas/<int:venta_id>/cancelar")
@ventas_required
def cancelar_pedido(venta_id):
    """Cancela la reserva en efectivo: libera el stock reservado sin tocar el stock fisico."""
    from app.supabase_client import get_admin_client
    motivo = (request.form.get("motivo") or "").strip()
    destino = _destino_seguro(url_for("ventas.detalle", venta_id=venta_id))
    try:
        pendientes = _pagos_efectivo_pendientes(venta_id)
        if not pendientes:
            flash("Esta venta no tiene un pago en efectivo pendiente (ya fue confirmada o cancelada).", "warning")
            return redirect(destino)
        for pg in pendientes:
            get_admin_client().table("pagos").update(
                {"estado": "RECHAZADO", "observaciones": motivo or "Reserva cancelada"}).eq("id", pg["id"]).execute()
        _resolver_stock(venta_id, cobrado=False)
        get_admin_client().table("ventas").update({"estado": "CANCELADA"}).eq("id", venta_id).execute()
        flash("Reserva cancelada: el stock reservado quedó disponible.", "warning")
    except Exception as exc:  # noqa: BLE001
        flash("No se pudo cancelar la reserva.", "error")
    return redirect(destino)
