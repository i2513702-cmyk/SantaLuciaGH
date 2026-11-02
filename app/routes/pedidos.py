"""Pedidos del cliente (historial y seguimiento de pagos manuales)."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.decorators import login_required
from app.supabase_client import get_admin_client, get_reader

bp = Blueprint("pedidos", __name__)


def _estado_badge(estado):
    mapa = {
        "PENDIENTE": ("Pendiente de pago", "text-bg-warning"),
        "PENDIENTE_PAGO": ("Pendiente de pago", "text-bg-warning"),
        "PENDIENTE_VERIFICACION": ("En verificacion", "text-bg-info"),
        "PAGADA": ("Pagado", "text-bg-success"),
        "CONFIRMADO": ("Pagado", "text-bg-success"),
        "RECHAZADO": ("Rechazado", "text-bg-danger"),
        "CANCELADA": ("Cancelado", "text-bg-secondary"),
    }
    return mapa.get((estado or "").upper(), (estado or "Pendiente", "text-bg-secondary"))


@bp.get("/pedidos/mis-pedidos")
@login_required
def mis_pedidos():
    usuario_id = session.get("usuario_id") if False else None
    from flask import session
    usuario_id = session.get("usuario_id")
    ventas = []
    try:
        ventas = (
            get_reader()
            .table("ventas")
            .select("id,codigo_pedido,fecha_venta,total,estado,metodo_pago_id,observaciones")
            .eq("usuario_id", usuario_id)
            .order("fecha_venta", desc=True)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001
        ventas = []

    # Enriquecer con metodo de pago
    metodos = {}
    try:
        rows = get_reader().table("metodos_pago").select("id,nombre,codigo").execute().data or []
        metodos = {r["id"]: r for r in rows}
    except Exception:  # noqa: BLE001
        metodos = {}
    for v in ventas:
        mp = metodos.get(v.get("metodo_pago_id"), {})
        v["metodo_nombre"] = mp.get("nombre") or mp.get("codigo") or "Pago"
        label, badge = _estado_badge(v.get("estado"))
        v["estado_label"] = label
        v["estado_badge"] = badge

    return render_template("pedidos/mis_pedidos.html", pedidos=ventas)


@bp.get("/pedidos/<int:venta_id>")
@login_required
def detalle(venta_id):
    from flask import session
    usuario_id = session.get("usuario_id")
    try:
        filas = get_reader().table("ventas").select("*").eq("id", venta_id).limit(1).execute().data or []
    except Exception:  # noqa: BLE001
        filas = []
    if not filas or filas[0].get("usuario_id") != usuario_id:
        flash("Pedido no encontrado.", "error")
        return redirect(url_for("pedidos.mis_pedidos"))
    venta = filas[0]
    items = get_reader().table("detalle_venta").select("*").eq("venta_id", venta_id).execute().data or []
    pagos = get_reader().table("pagos").select("*").eq("venta_id", venta_id).execute().data or []
    pago = pagos[0] if pagos else {}
    # metodo
    metodo = None
    try:
        mrows = get_reader().table("metodos_pago").select("nombre,codigo").eq("id", venta.get("metodo_pago_id")).limit(1).execute().data or []
        metodo = mrows[0] if mrows else None
    except Exception:  # noqa: BLE001
        metodo = None
    venta["metodo_nombre"] = (metodo or {}).get("nombre") or (metodo or {}).get("codigo") or "Pago"
    label, badge = _estado_badge(venta.get("estado"))
    venta["estado_label"] = label
    venta["estado_badge"] = badge
    return render_template("pedidos/detalle.html", venta=venta, items=items, pago=pago)


@bp.post("/pedidos/<int:venta_id>/comprobante")
@login_required
def subir_comprobante(venta_id):
    from flask import session
    import os, uuid
    from werkzeug.utils import secure_filename
    usuario_id = session.get("usuario_id")
    try:
        filas = get_reader().table("ventas").select("id,usuario_id,estado").eq("id", venta_id).limit(1).execute().data or []
    except Exception:  # noqa: BLE001
        filas = []
    if not filas or filas[0].get("usuario_id") != usuario_id:
        flash("Pedido no encontrado.", "error")
        return redirect(url_for("pedidos.mis_pedidos"))
    archivo = request.files.get("comprobante")
    numero_operacion = (request.form.get("numero_operacion") or "").strip()
    if not archivo or not archivo.filename:
        flash("Debes subir el comprobante.", "error")
        return redirect(url_for("pedidos.detalle", venta_id=venta_id))
    ext = os.path.splitext(archivo.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".pdf"):
        flash("Formato no permitido.", "error")
        return redirect(url_for("pedidos.detalle", venta_id=venta_id))
    archivo.seek(0, os.SEEK_END)
    size = archivo.tell()
    archivo.seek(0)
    if size > 5 * 1024 * 1024:
        flash("El comprobante no puede superar 5 MB.", "error")
        return redirect(url_for("pedidos.detalle", venta_id=venta_id))
    carpeta = os.path.join("app", "static", "uploads", "comprobantes")
    os.makedirs(carpeta, exist_ok=True)
    nombre = f"{uuid.uuid4().hex}{ext}"
    archivo.save(os.path.join(carpeta, nombre))
    ruta = f"uploads/comprobantes/{nombre}"
    try:
        from app.supabase_client import get_admin_client
        pagos = get_reader().table("pagos").select("id").eq("venta_id", venta_id).execute().data or []
        for pg in pagos:
            get_admin_client().table("pagos").update({
                "estado": "PENDIENTE_VERIFICACION",
                "observaciones": f"comprobante={ruta}; operacion={numero_operacion}",
            }).eq("id", pg["id"]).execute()
        get_admin_client().table("ventas").update({"estado": "PENDIENTE_VERIFICACION"}).eq("id", venta_id).execute()
        flash("Comprobante subido. Queda pendiente de verificacion.", "success")
    except Exception:  # noqa: BLE001
        flash("No se pudo guardar el comprobante.", "error")
    return redirect(url_for("pedidos.detalle", venta_id=venta_id))


@bp.get("/consultar-pedido")
def consultar_pedido():
    return render_template("pedidos/consultar.html", resultado=None)


@bp.post("/consultar-pedido")
def consultar_pedido_post():
    codigo = (request.form.get("codigo") or "").strip()
    documento = (request.form.get("documento") or "").strip()
    resultado = None
    try:
        filas = get_reader().table("ventas").select("id,codigo_pedido,fecha_venta,total,estado,metodo_pago_id,observaciones,cliente_id").eq("codigo_pedido", codigo).limit(1).execute().data or []
        if filas:
            venta = filas[0]
            # verificar documento
            cliente = None
            if venta.get("cliente_id"):
                crows = get_reader().table("clientes").select("numero_documento,telefono").eq("id", venta["cliente_id"]).limit(1).execute().data or []
                cliente = crows[0] if crows else None
            ok = False
            if cliente and documento and (cliente.get("numero_documento") == documento or cliente.get("telefono") == documento):
                ok = True
            if ok:
                label, badge = _estado_badge(venta.get("estado"))
                venta["estado_label"] = label
                venta["estado_badge"] = badge
                resultado = venta
            else:
                flash("Datos no coinciden.", "error")
    except Exception:  # noqa: BLE001
        flash("No se pudo consultar el pedido.", "error")
    return render_template("pedidos/consultar.html", resultado=resultado)
