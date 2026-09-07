"""Páginas públicas: inicio y tienda (catálogo de la base de datos)."""

from flask import Blueprint, render_template

from app.supabase_client import get_reader

bp = Blueprint("main", __name__)


def _precio(valor):
    try:
        return f"S/ {float(valor):,.2f}"
    except (TypeError, ValueError):
        return "—"


@bp.route("/")
def home():
    return render_template("home.html")


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