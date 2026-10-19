"""Páginas públicas: inicio, tienda (catálogo), ficha de producto y perfil."""

from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.decorators import ROLES_DB, es_no_comprador, login_required
from app.exceptions import AppError, ValidationError
from app.services import storage_service, tracking_service
from app.supabase_client import get_reader

bp = Blueprint("main", __name__)

# Días durante los que un producto recién creado se marca como "Nuevo".
DIAS_NOVEDAD = 30


def _precio(valor):
    try:
        return f"S/ {float(valor):,.2f}"
    except (TypeError, ValueError):
        return "—"


# Columnas del catálogo. Se leen con `imagen_url`; si esa columna aún no
# existe en Supabase (migración pendiente), se reintenta sin ella para no
# romper la tienda ni los destacados del inicio.
_COLS_CATALOGO = (
    "id,codigo,nombre,descripcion,modelo,tipo_producto,imagen_url,fecha_creacion,"
    "marca_id,categoria_id,precio_venta,precio_promocional,"
    "marcas(nombre),categorias(nombre)"
)
_COLS_CATALOGO_SIN_IMG = (
    "id,codigo,nombre,descripcion,modelo,tipo_producto,fecha_creacion,"
    "marca_id,categoria_id,precio_venta,precio_promocional,"
    "marcas(nombre),categorias(nombre)"
)


def _es_nuevo(valor, dias: int = DIAS_NOVEDAD) -> bool:
    """True si la fecha de creación está dentro de los últimos `dias`."""
    if not valor:
        return False
    try:
        fecha = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fecha) <= timedelta(days=dias)


def _mapear_producto(p: dict) -> dict:
    """Adapta una fila de `productos` a lo que esperan las plantillas.

    Calcula precios formateados, el porcentaje de descuento (para el badge
    "-20%") y si el producto es reciente (badge "Nuevo").
    """
    original = _precio(p.get("precio_venta"))
    promo = p.get("precio_promocional")
    promo_str = _precio(promo) if promo else None
    oferta = promo_str is not None

    descuento_pct = None
    if oferta:
        try:
            base = float(p.get("precio_venta") or 0)
            rebajado = float(promo)
            if base > 0 and rebajado < base:
                descuento_pct = round((base - rebajado) / base * 100)
        except (TypeError, ValueError):
            descuento_pct = None

    return {
        "id": p.get("id"),
        "codigo": p.get("codigo"),
        "nombre": p.get("nombre"),
        "descripcion": p.get("descripcion"),
        "modelo": p.get("modelo"),
        "tipo": p.get("tipo_producto"),
        "imagen_url": p.get("imagen_url"),
        "marca": (p.get("marcas") or {}).get("nombre"),
        "categoria": (p.get("categorias") or {}).get("nombre"),
        "marca_id": p.get("marca_id"),
        "categoria_id": p.get("categoria_id"),
        "precio_ahora": promo_str or original,
        "precio_antes": original if oferta else None,
        "oferta": oferta,
        "descuento_pct": descuento_pct,
        "nuevo": _es_nuevo(p.get("fecha_creacion")),
    }


def _leer_productos_activos(limite=None) -> list:
    """Lee el catálogo activo, incluyendo la imagen del producto si existe."""
    reader = get_reader()
    for cols in (_COLS_CATALOGO, _COLS_CATALOGO_SIN_IMG):
        try:
            q = reader.table("productos").select(cols).eq("activo", True).order("nombre")
            if limite:
                q = q.limit(limite)
            return q.execute().data or []
        except Exception:  # noqa: BLE001
            continue
    return []


def _leer_producto_por_id(producto_id: int):
    """Devuelve una fila de `productos` por id (o None si no existe)."""
    reader = get_reader()
    for cols in (_COLS_CATALOGO, _COLS_CATALOGO_SIN_IMG):
        try:
            filas = (
                reader.table("productos")
                .select(cols)
                .eq("id", producto_id)
                .limit(1)
                .execute()
                .data
            )
            return filas[0] if filas else None
        except Exception:  # noqa: BLE001
            continue
    return None


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
    productos = [_mapear_producto(p) for p in _leer_productos_activos(limite=6)]

    return {"marcas": marcas, "categorias": categorias, "productos_destacados": productos}


@bp.route("/")
def home():
    tracking_service.registrar_visita("/")
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
    """Catálogo real: búsqueda por palabra, filtros y índice de categorías.

    Los productos, categorías y marcas provienen de las tablas de Supabase
    (`productos`, `categorias`, `marcas`). Los filtros se aplican aquí sobre
    el listado activo para mantener una sola lectura de la BD.
    """
    q_display = request.args.get("q", "").strip()
    q = q_display.lower()
    cat_id = request.args.get("cat", "").strip()
    marca_id = request.args.get("marca", "").strip()
    solo_ofertas = request.args.get("oferta") == "on"

    filas = _leer_productos_activos()

    try:
        categorias_tabla = (
            get_reader().table("categorias").select("id,nombre").order("nombre").execute().data or []
        )
        marcas_tabla = (
            get_reader().table("marcas").select("id,nombre").order("nombre").execute().data or []
        )
    except Exception:  # noqa: BLE001
        categorias_tabla, marcas_tabla = [], []

    productos = [_mapear_producto(p) for p in filas]

    def _coincide(texto) -> bool:
        return q in (texto or "").lower()

    # Filtros salvo el de categoría (para que el índice refleje la búsqueda/marca)
    preseleccionados = [
        p for p in productos
        if (not marca_id or str(p["marca_id"] or "") == marca_id)
        and (not solo_ofertas or p["oferta"])
        and (not q or any(_coincide(t) for t in (
            p["nombre"], p["descripcion"], p["modelo"], p["marca"], p["categoria"]
        )))
    ]

    contador_cat = {}
    for p in preseleccionados:
        contador_cat[p["categoria_id"]] = contador_cat.get(p["categoria_id"], 0) + 1

    resultados = [
        p for p in preseleccionados
        if not cat_id or str(p["categoria_id"] or "") == cat_id
    ]

    def _url(**cambios):
        args = {}
        for k, v in (("q", q_display), ("cat", cat_id), ("marca", marca_id)):
            if v:
                args[k] = v
        if solo_ofertas:
            args["oferta"] = "on"
        args.update({k: v for k, v in cambios.items() if v})
        return url_for("main.tienda", **args)

    categorias_ui = []
    for c in categorias_tabla:
        count = contador_cat.get(c["id"], 0)
        if count == 0 and str(c["id"]) != cat_id:
            continue
        categorias_ui.append({
            "id": c["id"],
            "nombre": c["nombre"],
            "count": count,
            "url": _url(cat=str(c["id"])),
            "activa": str(c["id"]) == cat_id,
        })

    filtros_activos = bool(q_display or cat_id or marca_id or solo_ofertas)

    tracking_service.registrar_visita("/tienda")

    return render_template(
        "tienda.html",
        productos=resultados,
        categorias=categorias_ui,
        marcas=marcas_tabla,
        q=q_display,
        cat_id=cat_id,
        marca_id=marca_id,
        solo_ofertas=solo_ofertas,
        url_todo=_url(cat=""),
        url_limpiar=url_for("main.tienda"),
        n_resultados=len(resultados),
        filtros_activos=filtros_activos,
    )


@bp.route("/producto/<int:producto_id>")
def producto_detalle(producto_id):
    """Ficha pública de un producto del catálogo + relacionados."""
    fila = _leer_producto_por_id(producto_id)
    if not fila:
        abort(404)

    producto = _mapear_producto(fila)
    categoria_id = fila.get("categoria_id")
    relacionados = [
        _mapear_producto(p)
        for p in _leer_productos_activos()
        if p.get("id") != producto_id and p.get("categoria_id") == categoria_id
    ][:4]

    tracking_service.registrar_visita(f"/producto/{producto_id}")
    try:
        galeria = [
            f["url"]
            for f in storage_service.listar_fotos_producto(
                producto_id, producto.get("imagen_url")
            )
        ]
    except Exception:  # noqa: BLE001
        galeria = [producto["imagen_url"]] if producto.get("imagen_url") else []
    return render_template(
        "producto_detalle.html",
        producto=producto,
        relacionados=relacionados,
        galeria=galeria,
    )


@bp.route("/perfil")
@login_required
def perfil():
    """Cuenta del usuario logueado.

    Los clientes ven además su historial de compras. El personal interno
    solo accede a la gestión de su cuenta: no compra, así que no se consulta ni
    se muestra historial de pedidos.
    """
    usuario_id = session.get("usuario_id")
    usuario_nombre = session.get("nombre") or session.get("username")
    sin_compra = es_no_comprador(session.get("rol"))
    compras = []

    # --- Datos personales desde la BD (tabla usuarios + clientes/empleados) ---
    from app.services import rest_auth_service

    perfil_db = {}
    try:
        perfil_db = rest_auth_service.obtener_perfil(usuario_id)
    except Exception:  # noqa: BLE001
        perfil_db = {}

    if perfil_db.get("cliente_id") and (perfil_db.get("clientes") or not perfil_db.get("empleado_id")):
        persona = perfil_db.get("clientes") or {}
        origen_persona = "clientes"
    elif perfil_db.get("empleado_id"):
        persona = perfil_db.get("empleados") or {}
        origen_persona = "empleados"
    elif perfil_db.get("rol") == "CLIENTE":
        persona = perfil_db.get("clientes") or {}
        origen_persona = "clientes"
    else:
        persona = perfil_db.get("empleados") or {}
        origen_persona = "empleados"

    nombres = (persona.get("nombres") or "").strip()
    apellidos = (persona.get("apellidos") or "").strip()
    nombre_completo = " ".join(p for p in (nombres, apellidos) if p) or usuario_nombre

    tipo_doc = rest_auth_service.nombre_tipo_documento(persona.get("tipo_documento_id"))
    numero_doc = (persona.get("numero_documento") or "").strip()
    documento = f"{tipo_doc} {numero_doc}".strip() if numero_doc else ""

    ultimo_acceso = (perfil_db.get("ultimo_acceso") or "").replace("T", " ")[:16]

    # El correo de la cuenta (`usuarios.correo`) es la fuente de verdad del
    # perfil: es el que se edita al cambiar el dato en la base de datos. El
    # correo de la persona vinculada (`clientes`/`empleados`) puede quedar
    # desactualizado —o pertenecer a otro registro— por lo que solo se usa como
    # respaldo si la cuenta no tiene correo propio.
    correo_cuenta = (perfil_db.get("correo") or "").strip() or (persona.get("correo") or "").strip()

    telefono = (persona.get("telefono") or "").strip()
    cargo = (persona.get("cargo") or "").strip()
    es_empleado = origen_persona == "empleados"

    if not sin_compra:
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
        nombre_completo=nombre_completo,
        rol_label=ROLES_DB.get(session.get("rol") or "") or (session.get("rol") or ""),
        nombres=nombres,
        apellidos=apellidos,
        documento=documento,
        telefono=telefono,
        cargo=cargo,
        es_empleado=es_empleado,
        origen_persona=origen_persona,
        correo=correo_cuenta,
        ultimo_acceso=ultimo_acceso,
        compras=compras,
        mostrar_historial=not sin_compra,
    )


@bp.post("/perfil/datos")
@login_required
def perfil_datos():
    """Guarda los datos personales editables del usuario (el documento no cambia)."""
    from app.services import rest_auth_service

    try:
        rest_auth_service.actualizar_perfil(
            usuario_id=session.get("usuario_id"),
            nombres=request.form.get("nombres", ""),
            apellidos=request.form.get("apellidos", ""),
            correo=request.form.get("correo", ""),
            telefono=request.form.get("telefono", ""),
            cargo=request.form.get("cargo", ""),
        )
        flash("Datos personales actualizados correctamente.", "success")
    except (AppError, ValidationError) as exc:
        flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
    except Exception:  # noqa: BLE001
        flash("No se pudieron actualizar tus datos. Inténtalo de nuevo.", "error")

    return redirect(url_for("main.perfil"))


@bp.post("/perfil/avatar")
@login_required
def perfil_avatar():
    """Sube o elimina la foto de perfil del usuario (Supabase Storage)."""
    from app.services import storage_service

    usuario_id = session.get("usuario_id")
    accion = request.form.get("accion")
    try:
        if accion == "quitar":
            storage_service.quitar_avatar(usuario_id)
            session.pop("avatar_url", None)
            flash("Foto de perfil eliminada.", "success")
        else:
            archivo = request.files.get("avatar")
            if not archivo or not getattr(archivo, "filename", ""):
                raise ValidationError("Selecciona una imagen para tu foto de perfil.")
            url = storage_service.subir_avatar(archivo, usuario_id)
            session["avatar_url"] = url
            flash("Foto de perfil actualizada.", "success")
    except (AppError, ValidationError) as exc:
        flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
    except Exception:  # noqa: BLE001
        flash(
            "No se pudo actualizar la foto de perfil. Revisa el formato "
            "(PNG/JPG/WEBP) y el tamaño (máx 2 MB).",
            "error",
        )
    return redirect(url_for("main.perfil"))