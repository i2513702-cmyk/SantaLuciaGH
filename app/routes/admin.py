"""Panel de administración (datos reales vía API REST de Supabase)."""

from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app.decorators import ROLES_DB, roles_required
from app.exceptions import AppError, NotFoundError, ValidationError
from app.services import producto_service
from app.supabase_client import get_reader

bp = Blueprint("admin", __name__)

TABLAS_CONTEO = ("clientes", "productos", "tecnicos", "reparaciones", "usuarios")

# Columnas y armado de filas para el detalle de cada tarjeta de
# "Registro del sistema" (/admin/registro/<tabla>).
REGISTRO_TABLAS = {
    "tecnicos": {
        "titulo": "Técnicos",
        "select": "id,especialidad,empleados(nombres,apellidos)",
        "orden": "id",
    },
    "clientes": {
        "titulo": "Clientes",
        "select": "id,nombres,apellidos,razon_social,numero_documento,telefono,correo,activo",
        "orden": "id",
    },
    "productos": {
        "titulo": "Productos",
        "select": (
            "id,codigo,nombre,tipo_producto,precio_venta,precio_compra,activo,"
            "marcas(nombre),categorias(nombre)"
        ),
        "orden": "nombre",
    },
    "reparaciones": {
        "titulo": "Reparaciones",
        "select": "*",
        "orden": "fecha_ingreso",
        "desc": True,
    },
    "usuarios": {
        "titulo": "Usuarios",
        "select": (
            "id,nombre_usuario,correo,rol,activo,ultimo_acceso,"
            "empleados(nombres,apellidos),clientes(nombres,apellidos)"
        ),
        "orden": "id",
    },
}

ESTADOS_REPARACION_LABEL = {
    "RECIBIDO": "Recibido",
    "DIAGNOSTICO": "Diagnóstico",
    "ESPERANDO_APROBACION": "Esperando aprobación",
    "APROBADO": "Aprobado",
    "ESPERANDO_REPUESTO": "Esperando repuesto",
    "EN_REPARACION": "En reparación",
    "REPARADO": "Reparado",
    "LISTO_ENTREGA": "Listo para entrega",
    "ENTREGADO": "Entregado",
    "NO_REPARABLE": "No reparable",
    "CANCELADO": "Cancelado",
}

REPARACION_ACTIVA = (
    "RECIBIDO",
    "DIAGNOSTICO",
    "ESPERANDO_APROBACION",
    "APROBADO",
    "ESPERANDO_REPUESTO",
    "EN_REPARACION",
    "REPARADO",
    "LISTO_ENTREGA",
)


def _contar(tabla: str):
    """Devuelve el total de filas de una tabla; None si la consulta falla."""
    try:
        resp = get_reader().table(tabla).select("*", count="exact").limit(1).execute()
        return resp.count
    except Exception:  # noqa: BLE001
        return None


def _dia(fecha) -> str:
    """Normaliza un timestamp ISO a fecha 'YYYY-MM-DD'."""
    if not fecha:
        return "—"
    try:
        d = datetime.fromisoformat(str(fecha).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(fecha)[:10]
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.date().isoformat()


def _leer(tabla: str, select: str = "*", **kwargs):
    """Consulta robusta a una tabla; devuelve lista o [] ante errores."""
    try:
        q = get_reader().table(tabla).select(select)
        for col, val in kwargs.items():
            q = q.eq(col, val)
        return (q.execute().data) or []
    except Exception:  # noqa: BLE001
        return []


def _fecha_iso(valor):
    """Normaliza un timestamp a date (UTC); None si no es parseable."""
    try:
        d = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.date()


def _variacion(actual: float, anterior: float) -> float:
    """% de cambio entre dos periodos (robusto ante ceros)."""
    if anterior == 0:
        return 100.0 if actual and actual > 0 else 0.0
    return round((actual - anterior) / anterior * 100, 1)


def _pct_share(valor: float, total: float) -> float:
    """Porcentaje de participación (0 si el total es 0)."""
    if not total:
        return 0.0
    return round(valor / total * 100, 1)


def _metricas_dashboard():
    """Métricas reales orientadas a la toma de decisiones.

    Reúne: indicadores de venta, tendencias (mes actual vs anterior y últimos
    14 días), composición de ingresos (categorías y métodos de pago),
    rentabilidad (margen bruto), estado del taller (reparaciones y citas) e
    inventario (stock bajo, valor). Cierra con alertas accionables para el
    administrador. Ante cualquier fallo de una consulta se muestran 0/listas
    vacías sin romper el panel.
    """
    hoy = datetime.now(timezone.utc).date()

    # --- Ventas con cliente (para el ranking de clientes) ---
    ventas = _leer(
        "ventas",
        "id,total,estado,fecha_venta,usuario_id,cliente_id,"
        "clientes(nombres,apellidos,razon_social)",
    )
    ventas_pagadas = [v for v in ventas if v.get("estado") not in ("CANCELADA", "ANULADA")]

    # --- Detalle de venta (márgenes y composición por categoría) ---
    detalle = _leer(
        "detalle_venta",
        "id,producto_id,cantidad,precio_unitario,subtotal,venta_id,"
        "productos(categoria_id,precio_compra)",
    )
    productos = _leer(
        "productos",
        "id,nombre,marca_id,categoria_id,precio_venta,precio_compra,"
        "precio_promocional,activo,marcas(nombre)",
    )
    categorias_map = {c["id"]: c["nombre"] for c in _leer("categorias", "id,nombre")}
    metodos_map = {m["id"]: m["nombre"] for m in _leer("metodos_pago", "id,nombre")}
    clientes_map = {}
    for v in ventas:
        c = v.get("clientes") or {}
        if v.get("cliente_id") and c:
            clientes_map[v["cliente_id"]] = c

    inventario = _leer("inventario", "producto_id,stock_actual,stock_reservado")
    stock_map = {inv["producto_id"]: inv for inv in inventario}
    clientes_n = _contar("clientes") or 0

    # --- Totales generales ---
    total_ventas = round(sum(float(v.get("total") or 0) for v in ventas_pagadas), 2)
    n_ventas = len(ventas_pagadas)
    ticket_promedio = round(total_ventas / n_ventas, 2) if n_ventas else 0

    # --- Tendencias: mes actual vs anterior ---
    mes_act = (hoy.year, hoy.month)
    mes_prev = ((hoy.year - 1, 12) if hoy.month == 1 else (hoy.year, hoy.month - 1))

    def _mes_venta(v):
        d = _fecha_iso(v.get("fecha_venta"))
        return (d.year, d.month) if d else None

    sales_mes = [v for v in ventas_pagadas if _mes_venta(v) == mes_act]
    sales_prev = [v for v in ventas_pagadas if _mes_venta(v) == mes_prev]

    ventas_mes = round(sum(float(v.get("total") or 0) for v in sales_mes), 2)
    ventas_prev = round(sum(float(v.get("total") or 0) for v in sales_prev), 2)
    pedidos_mes = len(sales_mes)
    pedidos_prev = len(sales_prev)

    # --- Ventas por día (últimos 14 días) para el gráfico ---
    inicio = hoy - timedelta(days=13)
    por_dia = {inicio + timedelta(days=i): 0.0 for i in range(14)}
    for v in ventas_pagadas:
        d = _fecha_iso(v.get("fecha_venta"))
        if d and inicio <= d <= hoy:
            por_dia[d] += float(v.get("total") or 0)
    max_dia = max(por_dia.values()) or 1
    ventas_por_dia = [
        {
            "rotulo": fecha.strftime("%d %b"),
            "total": round(total, 2),
            "pct": round(total / max_dia * 100, 1),
        }
        for fecha, total in por_dia.items()
    ]

    # --- Rentabilidad (margen bruto sobre productos vendidos) ---
    productos_map = {p["id"]: p for p in productos}
    monto_categoria: dict = {}
    ingreso_lineas = 0.0
    costo_lineas = 0.0
    cantidad_por_producto: dict = {}
    ingreso_por_producto: dict = {}
    for d in detalle:
        prod = d.get("productos") or {}
        p = productos_map.get(d.get("producto_id")) or {}
        qty = float(d.get("cantidad") or 0)
        pu = float(d.get("precio_unitario") or 0)
        sub = float(d.get("subtotal") or pu * qty)

        ingreso_lineas += sub
        costo_lineas += qty * float(p.get("precio_compra") or 0)

        pid = d.get("producto_id")
        cantidad_por_producto[pid] = cantidad_por_producto.get(pid, 0) + qty
        ingreso_por_producto[pid] = ingreso_por_producto.get(pid, 0) + sub

        cat_id = prod.get("categoria_id") or p.get("categoria_id")
        monto_categoria[cat_id] = monto_categoria.get(cat_id, 0.0) + sub

    margen_bruto = round(ingreso_lineas - costo_lineas, 2)
    margen_pct = _pct_share(margen_bruto, ingreso_lineas) if ingreso_lineas else 0.0

    # --- Top productos (unidades e ingresos) ---
    top_productos = sorted(
        (
            {
                "producto_id": pid,
                "nombre": productos_map.get(pid, {}).get("nombre") or f"Producto #{pid}",
                "cantidad": round(qty, 2),
                "ingresos": round(ingreso_por_producto.get(pid, 0), 2),
            }
            for pid, qty in cantidad_por_producto.items()
        ),
        key=lambda x: x["cantidad"],
        reverse=True,
    )[:5]

    # --- Ingresos por categoría ---
    ventas_por_categoria = sorted(
        (
            {
                "nombre": categorias_map.get(cat_id, f"Categoría #{cat_id}"),
                "monto": round(monto, 2),
            }
            for cat_id, monto in monto_categoria.items()
        ),
        key=lambda x: x["monto"],
        reverse=True,
    )[:6]
    total_categorias = sum(c["monto"] for c in ventas_por_categoria)
    for c in ventas_por_categoria:
        c["pct"] = _pct_share(c["monto"], total_categorias)

    # --- Distribución de métodos de pago ---
    pagos = _leer("pagos", "id,venta_id,monto,estado,metodo_pago_id,fecha_pago")
    monto_por_metodo: dict = {}
    for p in pagos:
        if p.get("estado") not in ("CONFIRMADO", "COMPLETADO", "APROBADO"):
            continue
        mp = p.get("metodo_pago_id")
        monto_por_metodo[mp] = monto_por_metodo.get(mp, 0) + float(p.get("monto") or 0)
    total_metodos = sum(monto_por_metodo.values()) or 1
    metodos_max = max(monto_por_metodo.values()) if monto_por_metodo else 1
    metodos_pago = sorted(
        (
            {
                "nombre": metodos_map.get(mp, f"Método #{mp}"),
                "monto": round(valor, 2),
                "share": _pct_share(valor, total_metodos),
                "pct_max": round(valor / metodos_max * 100, 1),
            }
            for mp, valor in monto_por_metodo.items()
        ),
        key=lambda x: x["monto"],
        reverse=True,
    )

    # --- Top clientes por gasto ---
    cliente_stats: dict = {}
    for v in ventas_pagadas:
        cid = v.get("cliente_id")
        if not cid:
            continue
        stats = cliente_stats.setdefault(cid, {"nombre": "", "total": 0.0, "n": 0})
        cli = clientes_map.get(cid) or {}
        if not stats["nombre"]:
            stats["nombre"] = (
                cli.get("razon_social")
                or " ".join(filter(None, [cli.get("nombres"), cli.get("apellidos")]))
                or f"Cliente #{cid}"
            )
        stats["total"] += float(v.get("total") or 0)
        stats["n"] += 1
    top_clientes = [
        {"nombre": s["nombre"], "total": round(s["total"], 2), "n_pedidos": s["n"]}
        for s in cliente_stats.values()
    ]
    top_clientes.sort(key=lambda x: x["total"], reverse=True)

    # --- Taller: reparaciones por estado ---
    reparaciones = _leer("reparaciones", "id,estado")
    estados_count: dict = {}
    for r in reparaciones:
        estados_count[r.get("estado")] = estados_count.get(r.get("estado"), 0) + 1
    reparaciones_estado = [
        {
            "estado": estado,
            "label": ESTADOS_REPARACION_LABEL.get(estado, estado),
            "count": count,
        }
        for estado, count in sorted(estados_count.items(), key=lambda x: -x[1])
    ]
    reparaciones_activas = sum(c for k, c in estados_count.items() if k in REPARACION_ACTIVA)

    # --- Citas / reservas ---
    reservas = _leer("reservas", "id,estado,fecha_reserva,tecnico_id")
    citas_pendientes = [r for r in reservas if r.get("estado") == "PENDIENTE"]
    citas_confirmadas = [r for r in reservas if r.get("estado") == "CONFIRMADA"]
    citas_hoy = [r for r in reservas if (r.get("fecha_reserva") or "")[:10] == hoy.isoformat()]

    # --- Inventario: stock bajo, valor y productos activos sin ventas ---
    stock_bajo = []
    stock_total_valor = 0.0
    for p in productos:
        inv = stock_map.get(p.get("id"))
        if not inv:
            continue
        stock = float(inv.get("stock_actual") or 0)
        stock_total_valor += stock * float(p.get("precio_compra") or 0)
        if stock <= 10:
            stock_bajo.append({
                "nombre": p.get("nombre"),
                "stock": stock,
                "precio": round(float(p.get("precio_venta") or 0), 2),
                "critico": stock <= 3,
            })
    stock_bajo.sort(key=lambda s: s["stock"])
    stock_total_valor = round(stock_total_valor, 2)

    vendidos_ids = set(cantidad_por_producto.keys())
    productos_sin_ventas = [
        p["nombre"] for p in productos if p.get("activo") and p.get("id") not in vendidos_ids
    ][:6]

    # --- Ventas recientes (últimas 6 por fecha) ---
    ventas_recientes = sorted(
        ventas_pagadas,
        key=lambda v: v.get("fecha_venta") or "",
        reverse=True,
    )[:6]
    ventas_recientes = [
        {"codigo": v.get("codigo_pedido") or f"Venta #{v.get('id')}",
         "fecha": _dia(v.get("fecha_venta")),
         "total": round(float(v.get("total") or 0), 2)}
        for v in ventas_recientes
    ]

    # --- Alertas accionables para el administrador ---
    trend_v = _variacion(ventas_mes, ventas_prev)
    alertas = []
    n_criticos = sum(1 for s in stock_bajo if s["critico"])
    if n_criticos:
        alertas.append({
            "tipo": "crit",
            "icono": "📦",
            "titulo": f"{n_criticos} producto(s) con stock crítico (≤ 3 unidades)",
            "detalle": "Considera reabastecer antes de quedarte sin inventario.",
            "url": "admin.productos",
            "url_label": "Ver productos",
        })
    if citas_pendientes:
        alertas.append({
            "tipo": "warn",
            "icono": "🔧",
            "titulo": f"{len(citas_pendientes)} cita(s) sin técnico asignado",
            "detalle": "Asigna técnicos para evitar demoras en el servicio.",
            "url": "admin.citas_listado",
            "url_label": "Asignar técnicos",
        })
    if productos_sin_ventas:
        alertas.append({
            "tipo": "info",
            "icono": "🏷️",
            "titulo": f"{len(productos_sin_ventas)} producto(s) activos sin ventas",
            "detalle": "Revisa precios o promociona: " + ", ".join(productos_sin_ventas) + ".",
            "url": "admin.productos",
            "url_label": "Revisar catálogo",
        })
    if trend_v > 0:
        alertas.append({
            "tipo": "ok",
            "icono": "📈",
            "titulo": f"Ventas del mes +{trend_v}% frente al mes anterior",
            "detalle": f"S/ {ventas_prev:,.2f} en el mes anterior → S/ {ventas_mes:,.2f} este mes.",
        })
    elif trend_v < 0:
        alertas.append({
            "tipo": "warn",
            "icono": "📉",
            "titulo": f"Ventas del mes {abs(trend_v)}% bajo el mes anterior",
            "detalle": "Revisa promociones, stock y la atención de citas para retomar el ritmo.",
        })
    else:
        alertas.append({
            "tipo": "ok",
            "icono": "📊",
            "titulo": "Ventas del mes estables",
            "detalle": "Sin cambios respecto al mes anterior. Busca nuevas oportunidades.",
        })

    return {
        "total_ventas": total_ventas,
        "n_ventas": n_ventas,
        "ticket_promedio": ticket_promedio,
        "margen_bruto": margen_bruto,
        "margen_pct": margen_pct,
        "ventas_mes": ventas_mes,
        "ventas_prev": ventas_prev,
        "trend_ventas": trend_v,
        "pedidos_mes": pedidos_mes,
        "pedidos_prev": pedidos_prev,
        "trend_pedidos": _variacion(pedidos_mes, pedidos_prev),
        "ventas_por_dia": ventas_por_dia,
        "ventas_por_categoria": ventas_por_categoria,
        "top_productos": top_productos,
        "top_clientes": top_clientes,
        "metodos_pago": metodos_pago,
        "ventas_recientes": ventas_recientes,
        "stock_bajo": stock_bajo,
        "stock_total_valor": stock_total_valor,
        "reparaciones_estado": reparaciones_estado,
        "reparaciones_activas": reparaciones_activas,
        "n_citas_hoy": len(citas_hoy),
        "n_citas_pendientes": len(citas_pendientes),
        "n_citas_confirmadas": len(citas_confirmadas),
        "n_clientes": clientes_n,
        "alertas": alertas,
    }


def _registro_filas(tabla: str, cfg: dict) -> list:
    """Lee las filas de una tabla de "Registro del sistema" ya formateadas."""
    filas = _leer(tabla, cfg["select"])
    orden = cfg.get("orden")
    if orden:
        filas.sort(
            key=lambda r: (r.get(orden) is None, r.get(orden)),
            reverse=bool(cfg.get("desc")),
        )

    def _persona(rel):
        """Nombre legible desde una relación anidada (empleados/clientes)."""
        d = rel or {}
        return " ".join(filter(None, [d.get("nombres"), d.get("apellidos")])) or "—"

    if tabla == "tecnicos":
        return [{
            "celdas": [
                f"#{r.get('id')}",
                _persona(r.get("empleados")),
                r.get("especialidad") or "—",
            ],
            "badge": None,
            "busqueda": _persona(r.get("empleados")),
        } for r in filas]

    if tabla == "clientes":
        return [{
            "celdas": [
                f"#{r.get('id')}",
                r.get("razon_social") or _persona(r),
                r.get("numero_documento") or "—",
                r.get("telefono") or "—",
                r.get("correo") or "—",
            ],
            "badge": None,
            "busqueda": r.get("numero_documento") or "",
        } for r in filas]

    if tabla == "productos":
        return [{
            "celdas": [
                r.get("codigo") or f"#{r.get('id')}",
                r.get("nombre") or "—",
                (r.get("marcas") or {}).get("nombre") or "—",
                (r.get("categorias") or {}).get("nombre") or "—",
                f"S/ {float(r.get('precio_venta') or 0):,.2f}",
            ],
            "badge": None,
            "busqueda": r.get("codigo") or "",
        } for r in filas]

    if tabla == "reparaciones":
        return [{
            "celdas": [
                f"OS-{r.get('id')}",
                r.get("problema") or "—",
                _dia(r.get("fecha_ingreso")),
                r.get("tipo_atencion") or "—",
            ],
            "badge": {
                "texto": ESTADOS_REPARACION_LABEL.get(r.get("estado") or "", r.get("estado") or "—"),
                "clase": "badge-ok" if r.get("estado") in ("ENTREGADO", "REPARADO", "LISTO_ENTREGA")
                else "badge-warn" if r.get("estado") in ("EN_REPARACION", "DIAGNOSTICO", "APROBADO")
                else "badge-muted",
            },
            "busqueda": r.get("problema") or "",
        } for r in filas]

    # usuarios
    return [{
        "celdas": [
            f"#{r.get('id')}",
            r.get("nombre_usuario") or "—",
            r.get("correo") or "—",
            _persona(r.get("empleados")) if r.get("empleado_id") else _persona(r.get("clientes")),
            (r.get("ultimo_acceso") or "")[:10] or "—",
        ],
        "badge": {
            "texto": ROLES_DB.get(r.get("rol") or "", r.get("rol") or "—"),
            "clase": "badge-ok" if r.get("rol") == "ADMINISTRADOR" else "badge-muted",
        },
        "busqueda": r.get("correo") or "",
    } for r in filas]


REGISTRO_ENCABEZADOS = {
    "tecnicos": ("ID", "Técnico", "Especialidad"),
    "clientes": ("ID", "Cliente", "Documento", "Teléfono", "Correo"),
    "productos": ("Código", "Producto", "Marca", "Categoría", "Precio"),
    "reparaciones": ("Orden", "Problema", "Ingreso", "Atención", "Estado"),
    "usuarios": ("ID", "Usuario", "Correo", "Persona", "Último acceso", "Rol"),
}

REGISTRO_POR_PAGINA = 10

# Placeholder del input de búsqueda de cada tabla (None = sin buscador).
REGISTRO_BUSQUEDA = {
    "tecnicos": "Buscar por nombre...",
    "clientes": "Buscar por documento...",
    "productos": "Buscar por código...",
    "usuarios": "Buscar por correo...",
    "reparaciones": None,
}


@bp.get("/admin/registro/<tabla>")
@roles_required("admin")
def registro_detalle(tabla):
    """JSON paginado y filtrable del contenido de una tabla del registro."""
    cfg = REGISTRO_TABLAS.get(tabla)
    if not cfg:
        return jsonify({"ok": False, "detalle": "Tabla no encontrada."}), 404

    filas = _registro_filas(tabla, cfg)

    q = (request.args.get("q") or "").strip()
    if q:
        filas = [f for f in filas if q.lower() in (f["busqueda"] or "").lower()]

    total = len(filas)
    paginas = max(1, -(-total // REGISTRO_POR_PAGINA))
    try:
        pagina = int(request.args.get("pagina") or 1)
    except ValueError:
        pagina = 1
    pagina = min(max(pagina, 1), paginas)

    inicio = (pagina - 1) * REGISTRO_POR_PAGINA
    return jsonify({
        "ok": True,
        "titulo": cfg["titulo"],
        "encabezados": list(REGISTRO_ENCABEZADOS[tabla]),
        "filas": [
            {"celdas": f["celdas"], "badge": f["badge"]}
            for f in filas[inicio:inicio + REGISTRO_POR_PAGINA]
        ],
        "pagina": pagina,
        "paginas": paginas,
        "total": total,
        "por_pagina": REGISTRO_POR_PAGINA,
        "placeholder": REGISTRO_BUSQUEDA[tabla],
    })


@bp.route("/admin")
@roles_required("admin")
def dashboard():
    conteos = {t: _contar(t) for t in TABLAS_CONTEO}
    fecha_actualizacion = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")

    stats = [
        {"label": "Técnicos", "value": conteos.get("tecnicos") or 0, "icon": "🔧", "tabla": "tecnicos"},
        {"label": "Clientes", "value": conteos.get("clientes") or 0, "icon": "👥", "tabla": "clientes"},
        {"label": "Productos", "value": conteos.get("productos") or 0, "icon": "🏷️", "tabla": "productos"},
        {"label": "Reparaciones", "value": conteos.get("reparaciones") or 0, "icon": "🛠️", "tabla": "reparaciones"},
        {"label": "Usuarios", "value": conteos.get("usuarios") or 0, "icon": "🔐", "tabla": "usuarios"},
    ]
    metricas = _metricas_dashboard()
    modulos = [
        {
            "nombre": "Citas de servicio",
            "descripcion": "Ver citas y asignar técnico",
            "icon": "📅",
            "url": "admin.citas_listado",
        },
        {
            "nombre": "Productos",
            "descripcion": "Agregar, editar y eliminar productos del catálogo",
            "icon": "🏷️",
            "url": "admin.productos",
        },
        {
            "nombre": "Usuarios",
            "descripcion": "Administración de cuentas del sistema",
            "icon": "🔐",
            "url": "auth.usuarios_sistema",
        },
        {
            "nombre": "Cambiar contraseña",
            "descripcion": "Actualiza tu clave de acceso",
            "icon": "🔑",
            "url": "auth.cambiar_clave",
        },
        {
            "nombre": "Estado de Supabase",
            "descripcion": "Verifica la conexión con la base de datos",
            "icon": "🦾",
            "url": "supabase.status",
            "modal": "supabase",
        },
    ]
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        modulos=modulos,
        metricas=metricas,
        fecha_actualizacion=fecha_actualizacion,
    )


def _listar_tecnicos():
    """Lista de técnicos con datos del empleado (para asignar a citas)."""
    try:
        return (
            get_reader()
            .table("tecnicos")
            .select("id,especialidad,empleados(nombres,apellidos)")
            .order("id")
            .execute()
        ).data or []
    except Exception:  # noqa: BLE001
        return []


ESTADO_CITA_LABEL = {
    "PENDIENTE": "Pendiente",
    "CONFIRMADA": "Confirmada",
    "EN_ATENCION": "En atención",
    "COMPLETADA": "Completada",
    "CANCELADA": "Cancelada",
}


def _clase_estado_cita(estado: str) -> str:
    if estado == "COMPLETADA":
        return "badge-ok"
    if estado in ("CONFIRMADA", "EN_ATENCION"):
        return "badge-warn"
    if estado == "CANCELADA":
        return "badge-muted"
    return "badge-muted"


@bp.get("/admin/citas")
@roles_required("admin")
def citas_listado():
    """Lista las citas (reservas) para asignarles un técnico."""
    citas = []
    try:
        resp = (
            get_reader()
            .table("reservas")
            .select(
                "id,fecha_reserva,hora_inicio,hora_fin,estado,tipo_atencion,motivo,"
                "clientes(nombres,apellidos,telefono,numero_documento,razon_social),"
                "electrodomesticos(modelo,numero_serie,marcas(nombre),tipos_electrodomestico(nombre)),"
                "tecnicos(id,empleados(nombres,apellidos))"
            )
            .order("fecha_reserva", desc=True)
            .order("hora_inicio", desc=True)
            .execute()
        )
        filas = resp.data or []
    except Exception:  # noqa: BLE001
        filas = []

    for r in filas:
        estado = r.get("estado") or "PENDIENTE"
        cliente = r.get("clientes") or {}
        equipo = r.get("electrodomesticos") or {}
        tecnico = r.get("tecnicos") or {}
        citas.append({
            "id": r.get("id"),
            "fecha": (r.get("fecha_reserva") or "")[:10],
            "hora": r.get("hora_inicio"),
            "hora_fin": r.get("hora_fin"),
            "estado": ESTADO_CITA_LABEL.get(estado, estado),
            "badge_class": _clase_estado_cita(estado),
            "tipo_atencion": r.get("tipo_atencion"),
            "motivo": r.get("motivo"),
            "cliente_nombre": cliente.get("razon_social")
            or " ".join(filter(None, [cliente.get("nombres"), cliente.get("apellidos")])),
            "cliente_documento": cliente.get("numero_documento"),
            "cliente_telefono": cliente.get("telefono"),
            "equipo": " ".join(filter(None, [
                (equipo.get("tipos_electrodomestico") or {}).get("nombre"),
                (equipo.get("marcas") or {}).get("nombre"),
                equipo.get("modelo"),
            ])),
            "tecnico_id": (tecnico or {}).get("id"),
            "tecnico_nombre": " ".join(filter(None, [
                ((tecnico.get("empleados")) or {}).get("nombres"),
                ((tecnico.get("empleados")) or {}).get("apellidos"),
            ])) or "Sin asignar",
        })

    return render_template(
        "admin/citas.html",
        citas=citas,
        tecnicos=_listar_tecnicos(),
    )


@bp.post("/admin/citas/<int:reserva_id>/asignar")
@roles_required("admin")
def citas_asignar_tecnico(reserva_id):
    """Asigna (o libera) el técnico de una cita."""
    tecnico_id = request.form.get("tecnico_id", type=int)
    try:
        from app.supabase_client import get_admin_client

        get_admin_client().table("reservas").update(
            {"tecnico_id": tecnico_id or None, "estado": "CONFIRMADA" if tecnico_id else "PENDIENTE"}
        ).eq("id", reserva_id).execute()
        if tecnico_id:
            flash("Técnico asignado a la cita.", "success")
        else:
            flash("Cita liberada (técnico sin asignar).", "info")
    except Exception as exc:  # noqa: BLE001
        flash(f"No se pudo actualizar la cita: {exc}", "error")
    return redirect(url_for("admin.citas_listado"))


def _form_ctx(producto=None, valores=None):
    """Devuelve el contexto común del formulario de producto."""
    try:
        marcas = producto_service.listar_marcas()
        categorias = producto_service.listar_categorias()
    except ValidationError:
        marcas, categorias = [], []
    datos = dict(producto or {}) if producto else (dict(valores) if valores else {})
    if datos:
        for campo in ("marca_id", "categoria_id"):
            try:
                datos[campo] = int(datos.get(campo) or 0)
            except (TypeError, ValueError):
                datos[campo] = 0
        datos["activo"] = datos.get("activo") in (True, "on")
    return {"producto": datos, "marcas": marcas, "categorias": categorias}


@bp.route("/admin/productos")
@roles_required("admin")
def productos():
    try:
        lista = producto_service.listar_productos()
    except ValidationError as exc:
        flash(str(exc), "error")
        lista = []
    return render_template("admin/productos.html", productos=lista)


@bp.route("/admin/productos/nuevo", methods=["GET", "POST"])
@roles_required("admin")
def producto_nuevo():
    error = None
    valores = None
    if request.method == "POST":
        try:
            producto_service.crear_producto(request.form)
            flash("Producto creado correctamente.", "success")
            return redirect(url_for("admin.productos"))
        except (AppError, ValidationError) as exc:
            error = exc.message if isinstance(exc, AppError) else str(exc)
        except Exception:  # noqa: BLE001
            error = "No se pudo crear el producto. Revisa los datos e inténtalo de nuevo."
        valores = request.form
    ctx = _form_ctx(producto=valores)
    ctx["error"] = error
    return render_template("admin/producto_form.html", **ctx)


@bp.route("/admin/productos/<int:producto_id>/editar", methods=["GET", "POST"])
@roles_required("admin")
def producto_editar(producto_id):
    error = None
    valores = None
    if request.method == "POST":
        try:
            producto_service.actualizar_producto(producto_id, request.form)
            flash("Producto actualizado correctamente.", "success")
            return redirect(url_for("admin.productos"))
        except (AppError, ValidationError) as exc:
            error = exc.message if isinstance(exc, AppError) else str(exc)
        except Exception:  # noqa: BLE001
            error = "No se pudo actualizar el producto. Revisa los datos e inténtalo de nuevo."
        valores = request.form
    else:
        try:
            valores = producto_service.obtener_producto(producto_id)
        except (AppError, ValidationError) as exc:
            flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
            return redirect(url_for("admin.productos"))

    ctx = _form_ctx(producto=valores)
    ctx["error"] = error
    return render_template("admin/producto_form.html", **ctx)


@bp.post("/admin/productos/<int:producto_id>/eliminar")
@roles_required("admin")
def producto_eliminar(producto_id):
    try:
        producto_service.eliminar_producto(producto_id)
        flash("Producto eliminado correctamente.", "success")
    except (AppError, ValidationError) as exc:
        flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
    except Exception:  # noqa: BLE001
        flash("No se pudo eliminar el producto.", "error")
    return redirect(url_for("admin.productos"))