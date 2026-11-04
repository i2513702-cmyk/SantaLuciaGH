"""Endpoints públicos E01–E08 (inicio, servicios, opiniones y catálogo)."""

from __future__ import annotations

import json

from flask import jsonify, request

from app.api import bp
from app.api.common import (
    ApiError,
    api_route,
    arg_booleano,
    arg_entero,
    booleano,
    contar,
    correo_valido,
    dinero,
    fecha_iso,
    insert_row,
    now_utc_iso,
    paginacion,
    producto_por_id,
    producto_publico,
    productos_catalogo,
    productos_publicos,
    read_rows,
    stock_info,
    telefono_valido,
    texto,
)
from app.config import Config
from app.services import storage_service


SERVICIOS = [
    {
        "id": 1,
        "nombre": "Reparación de lavadoras",
        "descripcion": "Diagnóstico y reparación de lavadoras de todas las marcas, con repuestos originales y garantía.",
        "precio_desde": 45.0,
        "tiempo_estimado": "48h a 72h",
    },
    {
        "id": 2,
        "nombre": "Reparación de secadoras",
        "descripcion": "Solución de fallas eléctricas, de tambor y de resistencia en secadoras.",
        "precio_desde": 50.0,
        "tiempo_estimado": "48h a 72h",
    },
    {
        "id": 3,
        "nombre": "Reparación de refrigeradoras",
        "descripcion": "Recarga de gas, cambio de compresor y reparación del sistema de enfriamiento.",
        "precio_desde": 70.0,
        "tiempo_estimado": "72h a 96h",
    },
    {
        "id": 4,
        "nombre": "Reparación de congeladoras",
        "descripcion": "Mantenimiento y reparación de congeladoras horizontales y verticales.",
        "precio_desde": 75.0,
        "tiempo_estimado": "72h a 96h",
    },
    {
        "id": 5,
        "nombre": "Mantenimiento preventivo",
        "descripcion": "Limpieza, revisión y ajuste de electrodomésticos de línea blanca.",
        "precio_desde": 35.0,
        "tiempo_estimado": "24h a 48h",
    },
    {
        "id": 6,
        "nombre": "Cambio de tarjetas electrónicas",
        "descripcion": "Reparación y sustitución de tarjetas de control y potencia.",
        "precio_desde": 60.0,
        "tiempo_estimado": "48h a 72h",
    },
]

OPINIONES_DEFECTO = [
    {
        "autor": "María Quispe",
        "distrito": "El Tambo",
        "calificacion": 5,
        "comentario": "Repararon mi lavadora en dos días y me explicaron todo el trabajo. Muy recomendados.",
        "fecha": "2026-08-18",
    },
    {
        "autor": "Carlos Huamán",
        "distrito": "Huancayo",
        "calificacion": 5,
        "comentario": "El técnico llegó puntual a domicilio y dejó la refrigeradora funcionando perfecto.",
        "fecha": "2026-09-02",
    },
    {
        "autor": "Rosa Mendoza",
        "distrito": "Chilca",
        "calificacion": 4,
        "comentario": "Buen servicio y precios justos. El repuesto llegó un día después de lo previsto.",
        "fecha": "2026-09-21",
    },
    {
        "autor": "José Paredes",
        "distrito": "Pilcomayo",
        "calificacion": 5,
        "comentario": "Encontraron la falla de la secadora que otro taller no pudo resolver.",
        "fecha": "2026-10-05",
    },
]


def _opiniones(limite: int = 10) -> list[dict]:
    filas = read_rows(
        "notificaciones",
        "id,titulo,mensaje,fecha_creacion",
        tipo="OPINION",
        order="id",
        desc=True,
        limit=limite,
    )
    salida = []
    for fila in filas:
        try:
            datos = json.loads(fila.get("mensaje") or "{}")
        except (TypeError, ValueError):
            datos = {}
        salida.append(
            {
                "autor": datos.get("autor") or fila.get("titulo") or "Cliente",
                "distrito": datos.get("distrito") or "",
                "calificacion": int(datos.get("calificacion") or 5),
                "comentario": datos.get("comentario") or "",
                "fecha": fecha_iso(fila.get("fecha_creacion")),
            }
        )
    if not salida:
        salida = [dict(o) for o in OPINIONES_DEFECTO]
    return salida[:limite]


def _categorias_con_total() -> list[dict]:
    categorias = read_rows("categorias", "id,nombre,descripcion", order="nombre")
    productos = productos_catalogo(True)
    conteo = {}
    for p in productos:
        cid = p.get("categoria_id")
        conteo[cid] = conteo.get(cid, 0) + 1
    return [
        {
            "id": c.get("id"),
            "nombre": c.get("nombre"),
            "icono_url": None,
            "total_productos": conteo.get(c.get("id"), 0),
        }
        for c in categorias
    ]


@bp.get("/public/home")
@api_route
def public_home():
    marcas = read_rows("marcas", "id,nombre", order="nombre")
    categorias = _categorias_con_total()
    productos = productos_catalogo(True)

    def _orden_novedad(p):
        return str(p.get("fecha_creacion") or "")

    productos = sorted(productos, key=_orden_novedad, reverse=True)[:8]
    destacados = productos_publicos(productos)
    servicios = [dict(s) for s in SERVICIOS]
    opiniones = _opiniones(5)
    return jsonify(
        {
            "marcas": [
                {"id": m.get("id"), "nombre": m.get("nombre"), "logo_url": None}
                for m in marcas
            ],
            "categorias": [
                {"id": c.get("id"), "nombre": c.get("nombre"), "icono_url": c.get("icono_url")}
                for c in categorias
            ],
            "destacados": destacados,
            "servicios": servicios,
            "opiniones": opiniones,
            "estadisticas": {
                "anios_experiencia": 12,
                "reparaciones": contar("reparaciones") or 2500,
                "tecnicos": contar("tecnicos") or 4,
                "garantia_dias": 90,
            },
            "contacto": {
                "telefono": Config.CONTACTO_TELEFONO,
                "direccion": Config.CONTACTO_DIRECCION,
            },
        }
    )


@bp.get("/public/services")
@api_route
def public_services():
    return jsonify({"items": [dict(s) for s in SERVICIOS], "servicios": [dict(s) for s in SERVICIOS]})


@bp.get("/public/reviews")
@api_route
def public_reviews():
    limite = arg_entero("limit", 10, 1, 50)
    return jsonify({"items": _opiniones(limite), "opiniones": _opiniones(limite)})


@bp.post("/public/contact")
@api_route
def public_contact():
    data = request.get_json(silent=True) or request.form.to_dict()
    nombre = texto(data.get("nombre"))
    correo = texto(data.get("correo"))
    telefono = texto(data.get("telefono"))
    mensaje = texto(data.get("mensaje"))

    if not 2 <= len(nombre) <= 80:
        raise ApiError("El nombre debe tener entre 2 y 80 caracteres.", 400, "DATOS_INVALIDOS", {"nombre": "longitud"})
    if not correo_valido(correo):
        raise ApiError("El correo no tiene un formato válido.", 400, "DATOS_INVALIDOS", {"correo": "formato"})
    if telefono and not telefono_valido(telefono):
        raise ApiError("El teléfono debe tener hasta 9 dígitos.", 400, "DATOS_INVALIDOS", {"telefono": "formato"})
    if not 10 <= len(mensaje) <= 1000:
        raise ApiError("El mensaje debe tener entre 10 y 1000 caracteres.", 400, "DATOS_INVALIDOS", {"mensaje": "longitud"})

    fila = insert_row(
        "notificaciones",
        {
            "tipo": "CONTACTO",
            "titulo": nombre[:120],
            "mensaje": json.dumps(
                {"nombre": nombre, "correo": correo, "telefono": telefono, "mensaje": mensaje},
                ensure_ascii=False,
            ),
            "destinatario": correo,
            "canal": "FORMULARIO",
            "leida": False,
            "fecha_creacion": now_utc_iso(),
        },
    )
    return jsonify({"ok": True, "id": fila.get("id")}), 201


@bp.get("/catalog/brands")
@api_route
def catalog_brands():
    filas = read_rows("marcas", "id,nombre", order="nombre")
    items = [{"id": m.get("id"), "nombre": m.get("nombre"), "logo_url": None} for m in filas]
    return jsonify({"items": items, "marcas": items})


@bp.get("/catalog/categories")
@api_route
def catalog_categories():
    categorias = _categorias_con_total()
    return jsonify({"items": categorias, "categorias": categorias})


@bp.get("/products")
@api_route
def products_list():
    q = texto(request.args.get("q")).lower()
    categoria_id = arg_entero("categoria_id", 0, 0)
    marca_id = arg_entero("marca_id", 0, 0)
    oferta = arg_booleano("oferta", False)
    orden = texto(request.args.get("orden")).lower()
    page, limit, _offset = paginacion(20, 50)

    productos = productos_catalogo(True)
    filtrados = []
    for p in productos:
        marca = p.get("marcas") or {}
        categoria = p.get("categorias") or {}
        if q:
            heno = " ".join(
                str(x or "").lower()
                for x in (
                    p.get("nombre"),
                    p.get("codigo"),
                    p.get("modelo"),
                    p.get("descripcion"),
                    marca.get("nombre"),
                )
            )
            if q not in heno:
                continue
        if categoria_id and p.get("categoria_id") != categoria_id:
            continue
        if marca_id and p.get("marca_id") != marca_id:
            continue
        precio = dinero(p.get("precio_venta"))
        promo = p.get("precio_promocional")
        en_oferta = promo is not None and dinero(promo) < precio
        if oferta and not en_oferta:
            continue
        filtrados.append(p)

    if orden == "precio_asc":
        filtrados.sort(key=lambda p: dinero(p.get("precio_promocional") or p.get("precio_venta")))
    elif orden == "precio_desc":
        filtrados.sort(key=lambda p: dinero(p.get("precio_promocional") or p.get("precio_venta")), reverse=True)
    elif orden == "nombre":
        filtrados.sort(key=lambda p: str(p.get("nombre") or "").lower())
    else:
        filtrados.sort(key=lambda p: int(p.get("id") or 0), reverse=True)

    total = len(filtrados)
    pagina = filtrados[(page - 1) * limit : page * limit]
    items = productos_publicos(pagina)
    return jsonify({"items": items, "productos": items, "page": page, "limit": limit, "total": total})


@bp.get("/products/<int:producto_id>")
@api_route
def product_detail(producto_id: int):
    p = producto_por_id(producto_id)
    if not p or p.get("activo") is False:
        raise ApiError("El producto no existe o no está disponible.", 404, "NO_ENCONTRADO")
    stock = stock_info([producto_id]).get(producto_id, {}).get("disponible", 0)
    base = producto_publico(p, stock)
    marca = p.get("marcas") or {}
    categoria = p.get("categorias") or {}
    try:
        fotos = storage_service.listar_fotos_producto(producto_id, p.get("imagen_url"))
        imagenes = [f.get("url") for f in fotos if isinstance(f, dict) and f.get("url")]
    except Exception:  # noqa: BLE001
        imagenes = [p.get("imagen_url")] if p.get("imagen_url") else []
    from app.api.common import compatibilidad_producto

    compatibles = compatibilidad_producto(producto_id)
    base.update(
        {
            "descripcion": p.get("descripcion") or "",
            "marca_texto": marca.get("nombre") or "",
            "categoria_texto": categoria.get("nombre") or "",
            "modelo": p.get("modelo") or "",
            "precio_anterior": dinero(p.get("precio_venta")) if base.get("en_oferta") else None,
            "compatibilidad": compatibles,
            "modelos": [p.get("modelo")] if p.get("modelo") else [],
            "imagenes": [u for u in dict.fromkeys(imagenes) if u],
        }
    )
    return jsonify(base)
