"""Páginas públicas: inicio y tienda (catálogo de la base de datos)."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

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