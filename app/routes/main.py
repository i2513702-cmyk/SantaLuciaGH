"""Páginas públicas: inicio, tienda (catálogo) y perfil del usuario."""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.decorators import login_required
from app.supabase_client import get_reader

bp = Blueprint("main", __name__)


def _precio(valor):
    try:
        return f"S/ {float(valor):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _cargar_home_data() -> dict:
    """Carga datos públicos para la landing y la tienda."""
    reader = get_reader()

    def _q(tabla, cols="*", filtros=None, orden=None, lim=None):
        try:
            q = reader.table(tabla).select(cols)
            if filtros:
                for k, v in filtros.items():
                    q = q.eq(k, v)
            if orden:
                q = q.order(orden)
            if lim:
                q = q.limit(lim)
            return q.execute().data or []
        except Exception:  # noqa: BLE001
            return []

    marcas = [
        {"id": m["id"], "nombre": m["nombre"]}
        for m in _q("marcas", "id,nombre", orden="id")
    ]
    categorias = [
        {"id": c["id"], "nombre": c["nombre"]}
        for c in _q("categorias", "id,nombre", orden="id")
    ]
    productos_raw = _q(
        "productos",
        "id,nombre,precio_venta,precio_promocional,marcas(nombre),categorias(nombre)",
        filtros={"activo": True},
        orden="nombre",
        lim=6,
    )
    productos = []
    for p in productos_raw:
        original = _precio(p.get("precio_venta"))
        promocional = _precio(p.get("precio_promocional")) if p.get("precio_promocional") else None
        productos.append({
            "id": p.get("id"),
            "nombre": p.get("nombre"),
            "marca": (p.get("marcas") or {}).get("nombre"),
            "categoria": (p.get("categorias") or {}).get("nombre"),
            "precio_ahora": promocional or original,
            "precio_antes": original if promocional else None,
            "oferta": promocional is not None,
        })

    return {"marcas": marcas, "categorias": categorias, "productos_destacados": productos}


@bp.route("/")
def home():
    data = _cargar_home_data()
    return render_template("home.html", **data)


@bp.post("/contacto")
def contacto():
    """Recibe el mensaje del formulario del home (solo confirmación por ahora)."""
    nombre = (request.form.get("nombre") or "").strip()
    correo = (request.form.get("correo") or "").strip()
    mensaje = (request.form.get("mensaje") or "").strip()
    if nombre and correo and mensaje:
        flash("Gracias por escribirnos. Te responderemos con un estimado el mismo día.", "success")
    else:
        flash("Completa todos los campos del formulario.", "error")
    return redirect(url_for("main.home"))


@bp.route("/tienda")
def tienda():
    """Muestra los productos reales de la tabla `productos` (estilo vitrina)."""
    try:
        resp = (
            get_reader()
            .table("productos")
            .select(
                "id,nombre,descripcion,modelo,tipo_producto,precio_venta,"
                "precio_promocional,marcas(nombre),categorias(nombre)"
            )
            .eq("activo", True)
            .order("nombre")
            .execute()
        )
        filas = resp.data or []
    except Exception:  # noqa: BLE001
        filas = []

    productos = []
    for p in filas:
        original = _precio(p.get("precio_venta"))
        promocional = _precio(p.get("precio_promocional")) if p.get("precio_promocional") else None
        oferta = promocional is not None
        if oferta:
            precio_ahora, precio_antes = promocional, original
        else:
            precio_ahora, precio_antes = original, None
        productos.append({
            "id": p.get("id"),
            "nombre": p.get("nombre"),
            "marca": (p.get("marcas") or {}).get("nombre"),
            "categoria": (p.get("categorias") or {}).get("nombre"),
            "modelo": p.get("modelo"),
            "tipo": p.get("tipo_producto"),
            "descripcion": p.get("descripcion"),
            "precio_ahora": precio_ahora,
            "precio_antes": precio_antes,
            "oferta": oferta,
        })

    return render_template("tienda.html", productos=productos)


@bp.route("/perfil")
@login_required
def perfil():
    """Historial de compras del usuario logueado (ventas con usuario_id)."""
    usuario_id = session.get("usuario_id")
    usuario_nombre = session.get("nombre") or session.get("username")

    try:
        resp = (
            get_reader()
            .table("ventas")
            .select(
                "id,codigo_pedido,fecha_venta,subtotal,descuento,costo_envio,total,"
                "estado,tipo_entrega,canal_venta,"
                "comprobantes(tipo_comprobante,serie,numero),"
                "detalle_venta(cantidad,precio_unitario,productos(nombre,marcas(nombre)))"
            )
            .eq("usuario_id", usuario_id)
            .order("fecha_venta", desc=True)
            .execute()
        )
        filas = resp.data or []
    except Exception:  # noqa: BLE001
        filas = []

    compras = []
    for v in filas:
        lineas = []
        for dv in v.get("detalle_venta") or []:
            prod = dv.get("productos") or {}
            lineas.append({
                "producto": prod.get("nombre") or "Producto",
                "marca": (prod.get("marcas") or {}).get("nombre"),
                "cantidad": dv.get("cantidad"),
                "precio_unitario": _precio(dv.get("precio_unitario")),
            })
        comprobante = None
        comps = v.get("comprobantes") or []
        if comps:
            c = comps[0]
            serie = c.get("serie") or ""
            numero = c.get("numero") or ""
            comprobante = f"{c.get('tipo_comprobante')} {serie or ''}-{numero or ''}".strip()
        compras.append({
            "codigo": v.get("codigo_pedido") or f"PED-{v.get('id')}",
            "fecha": (v.get("fecha_venta") or "")[:10],
            "subtotal": _precio(v.get("subtotal")),
            "descuento": _precio(v.get("descuento")),
            "envio": _precio(v.get("costo_envio")),
            "total": _precio(v.get("total")),
            "estado": v.get("estado") or "PENDIENTE",
            "entrega": v.get("tipo_entrega"),
            "canal": v.get("canal_venta"),
            "comprobante": comprobante,
            "lineas": lineas,
            "n_lineas": len(lineas),
        })

    return render_template(
        "perfil.html",
        usuario_nombre=usuario_nombre,
        compras=compras,
    )