"""Endpoints del panel administrador E40–E66."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, StringIO

from flask import Response, jsonify, request

from app.api import bp
from app.api.common import (
    ApiError,
    api_auth,
    api_route,
    appointments_payload,
    arg_booleano,
    arg_entero,
    booleano,
    cita_por_id,
    compatibilidad_producto,
    contexto_actual,
    contar,
    cuerpo,
    datetime_iso,
    decimal,
    dinero,
    entero,
    fecha_iso,
    historial_precios,
    hoy_lima,
    insert_row,
    meta_guardar,
    meta_obtener,
    meta_producto,
    mover_stock,
    nombre_persona,
    notificar,
    now_utc_iso,
    paginacion,
    producto_por_id,
    productos_catalogo,
    read_one,
    read_rows,
    requerir,
    stock_info,
    stock_minimo_producto,
    texto,
    tipo_documento_id,
    update_row,
    usuario_por_cliente,
)
from app.config import Config
from app.services import cuenta_guard, kpi_service, login_guard, rest_auth_service, storage_service

ESTADOS_REPARACION_CERRADA = {"ENTREGADO", "FINALIZADO", "CANCELADO", "ANULADO", "RECHAZADO"}
ROLES = {
    "ADMINISTRADOR",
    "SUPERVISOR",
    "TECNICO",
    "RECEPCIONISTA",
    "VENDEDOR",
    "ALMACENERO",
    "CLIENTE",
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _dt(valor):
    if not valor:
        return None
    try:
        d = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _venta_valida(v: dict) -> bool:
    return (v.get("estado") or "").upper() not in {"CANCELADA", "ANULADA", "PAGO_RECHAZADO"}


def _ventas_todas() -> list[dict]:
    return read_rows("ventas", "id,total,estado,fecha_venta,cliente_id,usuario_id,codigo_pedido", order="id")


def _clientes_map() -> dict:
    return {c.get("id"): c for c in read_rows("clientes", "id,nombres,apellidos,razon_social,telefono,correo,numero_documento,fecha_registro,activo")}


def _resumen_ventas(ventas: list[dict], desde: date, hasta: date) -> list[dict]:
    salida = []
    for v in ventas:
        d = _dt(v.get("fecha_venta"))
        if d and desde <= d.date() <= hasta and _venta_valida(v):
            salida.append(v)
    return salida


def _variacion(actual: float, anterior: float) -> float:
    if not anterior:
        return 100.0 if actual else 0.0
    return round((actual - anterior) / anterior * 100, 1)


def _auditar(accion: str, objetivo: str, detalle: str = "") -> None:
    ctx = contexto_actual()
    try:
        login_guard.registrar_evento(
            "LOGIN_OK",
            ip=request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip(),
            usuario=objetivo,
            admin=ctx.get("usuario", {}).get("nombre_usuario"),
            detalle=f"{accion}: {detalle}".strip(": "),
        )
    except Exception:  # noqa: BLE001
        pass


def _persona_usuario(usuario: dict) -> tuple[str, int | None, dict]:
    if usuario.get("cliente_id"):
        fila = read_one("clientes", "*", id=usuario.get("cliente_id")) or {}
        return "clientes", usuario.get("cliente_id"), fila
    if usuario.get("empleado_id"):
        fila = read_one("empleados", "*", id=usuario.get("empleado_id")) or {}
        return "empleados", usuario.get("empleado_id"), fila
    return "", None, {}


def _tecnico_usuario(usuario: dict) -> dict | None:
    if not usuario.get("empleado_id"):
        return None
    return read_one("tecnicos", "*", empleado_id=usuario.get("empleado_id"))


def _bloqueado(usuario: dict) -> bool:
    try:
        return cuenta_guard.segundos_de_espera(usuario) > 0
    except Exception:  # noqa: BLE001
        return False


def _usuario_payload(usuario: dict, incluir_detalle: bool = False) -> dict:
    tabla, _fila_id, persona = _persona_usuario(usuario)
    data = {
        "id": usuario.get("id"),
        "nombres": persona.get("nombres") or "",
        "apellidos": persona.get("apellidos") or "",
        "usuario": usuario.get("nombre_usuario"),
        "correo": usuario.get("correo"),
        "rol": usuario.get("rol"),
        "activo": bool(usuario.get("activo")),
        "bloqueado": _bloqueado(usuario),
        "ultima_actividad": datetime_iso(usuario.get("ultimo_acceso")),
    }
    if incluir_detalle:
        data["tipo_documento"] = rest_auth_service.nombre_tipo_documento(persona.get("tipo_documento_id"))
        data["numero_documento"] = persona.get("numero_documento") or ""
        data["telefono"] = persona.get("telefono") or ""
        tecnico = _tecnico_usuario(usuario) if (usuario.get("rol") or "").upper() == "TECNICO" else None
        if tecnico:
            data["tecnico"] = {
                "especialidad": tecnico.get("especialidad") or "",
                "nivel": tecnico.get("nivel") or "",
            }
    return data


def _usuarios_full() -> list[dict]:
    filas = read_rows(
        "usuarios",
        "*,empleados(nombres,apellidos,telefono,correo,cargo,tipo_documento_id,numero_documento),"
        "clientes(nombres,apellidos,telefono,correo,tipo_documento_id,numero_documento,razon_social)",
        order="id",
    )
    if filas:
        return filas
    return read_rows("usuarios", "*", order="id")


def _producto_payload(p: dict, stocks: dict, metas: dict, incluir_costo: bool) -> dict:
    pid = p.get("id")
    stock = stocks.get(pid, {}).get("disponible", 0)
    minimo = int((metas.get(pid) or {}).get("stock_minimo", 10) or 10)
    activo = p.get("activo") is not False
    if not activo:
        estado = "INACTIVO"
    elif stock < minimo:
        estado = "BAJO_STOCK"
    else:
        estado = "ACTIVO"
    marca = p.get("marcas") or {}
    categoria = p.get("categorias") or {}
    data = {
        "id": pid,
        "sku": p.get("codigo") or f"PRO-{pid}",
        "nombre": p.get("nombre") or "",
        "marca": marca.get("nombre") or "",
        "categoria": categoria.get("nombre") or "",
        "precio": dinero(p.get("precio_promocional") or p.get("precio_venta")),
        "stock": int(round(float(stock or 0))),
        "stock_minimo": minimo,
        "estado": estado,
        "imagen_url": p.get("imagen_url"),
        "en_catalogo_web": bool(activo),
    }
    if incluir_costo:
        data["costo"] = dinero(p.get("precio_compra"))
    return data


def _producto_detalle(producto_id: int, incluir_costo: bool) -> dict:
    p = producto_por_id(producto_id)
    if not p:
        raise ApiError("El producto no existe.", 404, "NO_ENCONTRADO")
    stocks = stock_info([producto_id])
    metas = {producto_id: meta_producto(producto_id)}
    data = _producto_payload(p, stocks, metas, incluir_costo)
    data.update(
        {
            "descripcion": p.get("descripcion") or "",
            "precio_oferta": (
                dinero(p.get("precio_promocional"))
                if p.get("precio_promocional") not in (None, "")
                else None
            ),
            "compatibilidad": compatibilidad_producto(producto_id),
            "historial_precios": historial_precios(producto_id, p.get("precio_venta")),
        }
    )
    return data


def _filtrar_citas(reservas: list[dict], clientes: dict) -> list[dict]:
    filtro = texto(request.args.get("q")).lower()
    estado = texto(request.args.get("estado")).upper()
    fecha = texto(request.args.get("fecha"))
    tecnico_id = arg_entero("tecnico_id", 0, 0)
    tipo = texto(request.args.get("tipo_atencion")).upper()
    salida = []
    for r in reservas:
        if estado and (r.get("estado") or "").upper() != estado:
            continue
        if fecha and fecha_iso(r.get("fecha_reserva")) != fecha:
            continue
        if tecnico_id and int(r.get("tecnico_id") or 0) != tecnico_id:
            continue
        if tipo and (r.get("tipo_atencion") or "").upper() != tipo:
            continue
        if filtro:
            cliente = clientes.get(r.get("cliente_id")) or {}
            texto_busqueda = " ".join(
                [
                    f"CIT-{int(r.get('id')):04d}",
                    nombre_persona(cliente),
                    str(r.get("calle") or ""),
                    str(r.get("motivo") or ""),
                ]
            ).lower()
            if filtro not in texto_busqueda:
                continue
        salida.append(r)
    salida.sort(key=lambda r: (str(r.get("fecha_reserva") or ""), str(r.get("hora_inicio") or "")), reverse=True)
    return salida


# ---------------------------------------------------------------------------
# Dashboard E40–E47
# ---------------------------------------------------------------------------

@bp.get("/admin/dashboard/summary")
@api_route
@api_auth("ADMINISTRADOR")
def admin_dashboard_summary():
    hoy = hoy_lima()
    ventas = _ventas_todas()
    mes_actual = [v for v in ventas if _venta_valida(v) and (_dt(v.get("fecha_venta")) or datetime.min.replace(tzinfo=timezone.utc)).date().replace(day=1) == hoy.replace(day=1)]
    anterior = (hoy.replace(day=1) - timedelta(days=1)).replace(day=1)
    mes_anterior = [v for v in ventas if _venta_valida(v) and (_dt(v.get("fecha_venta")) or datetime.min.replace(tzinfo=timezone.utc)).date().replace(day=1) == anterior]
    total_actual = sum(dinero(v.get("total")) for v in mes_actual)
    total_anterior = sum(dinero(v.get("total")) for v in mes_anterior)

    reservas_hoy = read_rows("reservas", "*", fecha_reserva=hoy.isoformat())
    reparaciones = read_rows("reparaciones", "id,estado,tipo_atencion")
    activas = [r for r in reparaciones if (r.get("estado") or "").upper() not in ESTADOS_REPARACION_CERRADA]
    clientes = read_rows("clientes", "id,fecha_registro")
    hace_7 = hoy - timedelta(days=7)
    nuevos = 0
    for c in clientes:
        d = _dt(c.get("fecha_registro"))
        if d and d.date() >= hace_7:
            nuevos += 1
    en_ruta = {r.get("tecnico_id") for r in read_rows("reservas", "tecnico_id,estado", estado="EN_CAMINO") if r.get("tecnico_id")}
    return jsonify(
        {
            "ventas_mes": {"monto": round(total_actual, 2), "variacion_pct": _variacion(total_actual, total_anterior)},
            "tickets_hoy": {
                "total": len(reservas_hoy),
                "por_asignar": sum(1 for r in reservas_hoy if (r.get("estado") or "").upper() == "PENDIENTE"),
            },
            "reparaciones_activas": {
                "total": len(activas),
                "domicilio": sum(1 for r in activas if (r.get("tipo_atencion") or "").upper() == "DOMICILIO"),
                "taller": sum(1 for r in activas if (r.get("tipo_atencion") or "").upper() != "DOMICILIO"),
            },
            "clientes": {"total": len(clientes), "nuevos_semana": nuevos, "tecnicos_en_ruta": len(en_ruta)},
        }
    )


@bp.get("/admin/dashboard/kpis")
@api_route
@api_auth("ADMINISTRADOR")
def admin_dashboard_kpis():
    hoy = hoy_lima()
    desde = texto(request.args.get("desde"))
    hasta = texto(request.args.get("hasta"))
    try:
        d1 = date.fromisoformat(desde) if desde else hoy - timedelta(days=29)
        d2 = date.fromisoformat(hasta) if hasta else hoy
    except ValueError as exc:
        raise ApiError("Las fechas no son válidas (YYYY-MM-DD).", 400, "DATOS_INVALIDOS") from exc
    if d1 > d2:
        raise ApiError("La fecha desde no puede ser mayor que hasta.", 400, "DATOS_INVALIDOS")
    data = kpi_service.calcular(d1, d2)
    nombres = {
        "KPI-01": "TIEMPO_RESPUESTA",
        "KPI-02": "ATENCION_24H",
        "KPI-03": "CONVERSION_WEB",
        "KPI-04": "DISPONIBILIDAD_REPUESTOS",
        "KPI-05": "ABANDONO_CARRITO",
        "KPI-06": "ENTREGA_NOTIFICACIONES",
    }
    items = []
    for k in data.get("kpis", []):
        valor = k.get("valor")
        variacion = k.get("variacion")
        try:
            factor = float(variacion)
        except (TypeError, ValueError):
            factor = 0.0
        tendencia = "sube" if factor > 0 else "baja" if factor < 0 else "estable"
        items.append(
            {
                "codigo": nombres.get(k.get("codigo"), k.get("codigo")),
                "nombre": k.get("nombre") or "",
                "valor": valor if valor is not None else 0,
                "unidad": k.get("unidad") or "",
                "meta": decimal(k.get("meta")),
                "cumple": bool(k.get("cumple")),
                "tendencia": tendencia,
            }
        )
    return jsonify({"items": items, "desde": data.get("desde"), "hasta": data.get("hasta")})


@bp.get("/admin/dashboard/sales")
@api_route
@api_auth("ADMINISTRADOR")
def admin_dashboard_sales():
    dias = arg_entero("dias", 14, 1, 90)
    hoy = hoy_lima()
    desde = hoy - timedelta(days=dias - 1)
    ventas = _resumen_ventas(_ventas_todas(), desde, hoy)
    por_dia = {desde + timedelta(days=i): {"monto": 0.0, "pedidos": 0} for i in range(dias)}
    for v in ventas:
        d = _dt(v.get("fecha_venta"))
        if d and d.date() in por_dia:
            por_dia[d.date()]["monto"] += dinero(v.get("total"))
            por_dia[d.date()]["pedidos"] += 1
    total = round(sum(x["monto"] for x in por_dia.values()), 2)
    con_ventas = sum(1 for x in por_dia.values() if x["pedidos"])
    serie = [
        {"fecha": f.isoformat(), "monto": round(x["monto"], 2), "pedidos": x["pedidos"]}
        for f, x in por_dia.items()
    ]
    return jsonify(
        {
            "total": total,
            "promedio_diario": round(total / dias, 2),
            "cumplimiento_pct": round(con_ventas / dias * 100, 1) if dias else 0.0,
            "serie": serie,
        }
    )


@bp.get("/admin/dashboard/revenue")
@api_route
@api_auth("ADMINISTRADOR")
def admin_dashboard_revenue():
    agrupar = texto(request.args.get("agrupar")).lower()
    if agrupar not in {"categoria", "metodo_pago"}:
        raise ApiError("agrupar debe ser categoria o metodo_pago.", 400, "DATOS_INVALIDOS")
    hoy = hoy_lima()
    desde_txt = texto(request.args.get("desde"))
    hasta_txt = texto(request.args.get("hasta"))
    try:
        desde = date.fromisoformat(desde_txt) if desde_txt else hoy - timedelta(days=29)
        hasta = date.fromisoformat(hasta_txt) if hasta_txt else hoy
    except ValueError as exc:
        raise ApiError("Las fechas no son válidas.", 400, "DATOS_INVALIDOS") from exc
    ventas = _resumen_ventas(_ventas_todas(), desde, hasta)
    ids_venta = {v.get("id") for v in ventas}
    grupos = defaultdict(float)
    if agrupar == "categoria":
        productos = {p.get("id"): p for p in productos_catalogo(None)}
        cats = {c.get("id"): c.get("nombre") for c in read_rows("categorias", "id,nombre")}
        detalle = read_rows("detalle_venta", "id,venta_id,producto_id,subtotal,precio_unitario,cantidad", order="id")
        for d in detalle:
            if d.get("venta_id") not in ids_venta:
                continue
            p = productos.get(d.get("producto_id")) or {}
            clave = cats.get(p.get("categoria_id")) or "Sin categoría"
            grupos[clave] += dinero(d.get("subtotal") if d.get("subtotal") is not None else dinero(d.get("precio_unitario")) * entero(d.get("cantidad")))
    else:
        pagos = read_rows("pagos", "id,venta_id,metodo_pago_id,estado", order="id")
        metodos = {m.get("id"): m.get("nombre") for m in read_rows("metodos_pago", "id,nombre")}
        for p in pagos:
            if p.get("venta_id") not in ids_venta:
                continue
            clave = metodos.get(p.get("metodo_pago_id")) or "Sin método"
            venta = next((v for v in ventas if v.get("id") == p.get("venta_id")), None)
            if venta:
                grupos[clave] += dinero(venta.get("total"))
    total = sum(grupos.values())
    items = [
        {"clave": k, "monto": round(v, 2), "porcentaje": round(v / total * 100, 1) if total else 0.0}
        for k, v in sorted(grupos.items(), key=lambda x: x[1], reverse=True)
    ]
    return jsonify({"items": items, "total": round(total, 2)})


@bp.get("/admin/dashboard/repairs")
@api_route
@api_auth("ADMINISTRADOR")
def admin_dashboard_repairs():
    hoy = hoy_lima()
    desde_txt = texto(request.args.get("desde"))
    hasta_txt = texto(request.args.get("hasta"))
    try:
        desde = date.fromisoformat(desde_txt) if desde_txt else None
        hasta = date.fromisoformat(hasta_txt) if hasta_txt else None
    except ValueError as exc:
        raise ApiError("Las fechas no son válidas.", 400, "DATOS_INVALIDOS") from exc
    reparaciones = read_rows("reparaciones", "id,estado,fecha_creacion")
    conteo = defaultdict(int)
    for r in reparaciones:
        d = _dt(r.get("fecha_creacion"))
        if desde and (not d or d.date() < desde):
            continue
        if hasta and (not d or d.date() > hasta):
            continue
        conteo[(r.get("estado") or "SIN_ESTADO").upper()] += 1
    items = [{"estado": k, "cantidad": v} for k, v in sorted(conteo.items())]
    return jsonify({"items": items})


@bp.get("/admin/dashboard/top")
@api_route
@api_auth("ADMINISTRADOR")
def admin_dashboard_top():
    tipo = texto(request.args.get("tipo")).lower()
    limite = arg_entero("limit", 5, 1, 20)
    if tipo not in {"productos", "clientes"}:
        raise ApiError("tipo debe ser productos o clientes.", 400, "DATOS_INVALIDOS")
    if tipo == "productos":
        detalle = read_rows("detalle_venta", "producto_id,cantidad,subtotal,precio_unitario", order="id")
        productos = {p.get("id"): p for p in productos_catalogo(None)}
        acumulado = {}
        for d in detalle:
            pid = d.get("producto_id")
            item = acumulado.setdefault(pid, {"id": pid, "unidades": 0, "monto": 0.0})
            item["unidades"] += entero(d.get("cantidad"))
            item["monto"] += dinero(d.get("subtotal") if d.get("subtotal") is not None else dinero(d.get("precio_unitario")) * entero(d.get("cantidad")))
        items = []
        for pid, item in acumulado.items():
            p = productos.get(pid) or {}
            items.append(
                {
                    "id": pid,
                    "nombre": p.get("nombre") or f"Producto #{pid}",
                    "sku": p.get("codigo") or "",
                    "unidades": item["unidades"],
                    "monto": round(item["monto"], 2),
                }
            )
        items.sort(key=lambda x: (x["unidades"], x["monto"]), reverse=True)
    else:
        clientes = _clientes_map()
        ventas = _ventas_todas()
        acumulado = {}
        for v in ventas:
            if not _venta_valida(v):
                continue
            cid = v.get("cliente_id")
            item = acumulado.setdefault(cid, {"id": cid, "monto": 0.0, "pedidos": 0})
            item["monto"] += dinero(v.get("total"))
            item["pedidos"] += 1
        items = []
        for cid, item in acumulado.items():
            c = clientes.get(cid) or {}
            items.append(
                {
                    "id": cid,
                    "nombre": nombre_persona(c) or f"Cliente #{cid}",
                    "monto": round(item["monto"], 2),
                    "pedidos": item["pedidos"],
                }
            )
        items.sort(key=lambda x: x["monto"], reverse=True)
    return jsonify({"items": items[:limite]})


@bp.get("/admin/dashboard/low-stock")
@api_route
@api_auth("ADMINISTRADOR", "ALMACENERO")
def admin_dashboard_low_stock():
    productos = productos_catalogo(None)
    stocks = stock_info([p.get("id") for p in productos])
    items = []
    for p in productos:
        pid = p.get("id")
        actual = stocks.get(pid, {}).get("disponible", 0)
        minimo = stock_minimo_producto(pid)
        if actual < minimo:
            items.append(
                {
                    "producto_id": pid,
                    "sku": p.get("codigo") or f"PRO-{pid}",
                    "nombre": p.get("nombre") or "",
                    "stock_actual": int(round(float(actual))),
                    "stock_minimo": minimo,
                }
            )
    items.sort(key=lambda x: (x["stock_actual"] - x["stock_minimo"], x["nombre"]))
    return jsonify({"total": len(items), "items": items})


@bp.get("/admin/dashboard/recent-sales")
@api_route
@api_auth("ADMINISTRADOR", "SUPERVISOR", "VENDEDOR")
def admin_dashboard_recent_sales():
    limite = arg_entero("limit", 10, 1, 50)
    ventas = sorted(_ventas_todas(), key=lambda v: int(v.get("id") or 0), reverse=True)[:limite]
    clientes = _clientes_map()
    pagos = read_rows("pagos", "venta_id,metodo_pago_id", order="id")
    metodos = {m.get("id"): m.get("nombre") for m in read_rows("metodos_pago", "id,nombre")}
    pago_por_venta = {}
    for p in pagos:
        pago_por_venta[p.get("venta_id")] = metodos.get(p.get("metodo_pago_id")) or ""
    return jsonify(
        {
            "items": [
                {
                    "pedido_id": v.get("id"),
                    "codigo": v.get("codigo_pedido"),
                    "cliente": nombre_persona(clientes.get(v.get("cliente_id"))) or "Cliente",
                    "total": dinero(v.get("total")),
                    "metodo_pago": pago_por_venta.get(v.get("id"), ""),
                    "fecha": datetime_iso(v.get("fecha_venta")),
                }
                for v in ventas
            ]
        }
    )


# ---------------------------------------------------------------------------
# Citas E48–E52
# ---------------------------------------------------------------------------

@bp.get("/admin/appointments")
@api_route
@api_auth("ADMINISTRADOR", "SUPERVISOR", "RECEPCIONISTA")
def admin_appointments_list():
    page, limit, _offset = paginacion(20, 50)
    todas = read_rows("reservas", "*", order="fecha_reserva", desc=True)
    clientes = _clientes_map()
    conteos = {
        "todas": len(todas),
        "pendientes": sum(1 for r in todas if (r.get("estado") or "").upper() == "PENDIENTE"),
        "asignadas": sum(1 for r in todas if (r.get("estado") or "").upper() == "ASIGNADA"),
        "en_camino": sum(1 for r in todas if (r.get("estado") or "").upper() == "EN_CAMINO"),
    }
    filtradas = _filtrar_citas(todas, clientes)
    total = len(filtradas)
    pagina = filtradas[(page - 1) * limit : page * limit]
    items = appointments_payload(pagina)
    for item in items:
        item.pop("_meta", None)
    asignadas = sum(1 for r in todas if r.get("tecnico_id"))
    return jsonify(
        {
            "items": items,
            "conteos": conteos,
            "eficacia_despacho": {"asignadas_a_tiempo": asignadas, "total": asignadas},
            "page": page,
            "limit": limit,
            "total": total,
        }
    )


@bp.get("/admin/appointments/<int:reserva_id>/technicians")
@api_route
@api_auth("ADMINISTRADOR", "SUPERVISOR", "RECEPCIONISTA")
def admin_appointment_technicians(reserva_id: int):
    reserva = cita_por_id(reserva_id)
    if not reserva:
        raise ApiError("La cita no existe.", 404, "NO_ENCONTRADO")
    fecha = fecha_iso(reserva.get("fecha_reserva"))
    inicio = str(reserva.get("hora_inicio") or "")[:5]
    fin = str(reserva.get("hora_fin") or "")[:5]
    ocupadas = read_rows("reservas", "tecnico_id,hora_inicio,hora_fin,estado", fecha_reserva=fecha)
    tecnicos = read_rows("tecnicos", "id,empleado_id,nivel,especialidad", order="id")
    empleados = {e.get("id"): e for e in read_rows("empleados", "id,nombres,apellidos")}
    estados = {}
    for t in tecnicos:
        # Un técnico está ocupado si tiene otra cita que se cruza con la franja.
        ocupado = False
        for r in ocupadas:
            if r.get("tecnico_id") != t.get("id") or int(r.get("id") or 0) == reserva_id:
                continue
            if (r.get("estado") or "").upper() == "CANCELADA":
                continue
            hi = str(r.get("hora_inicio") or "")[:5]
            hf = str(r.get("hora_fin") or "")[:5]
            if hi and hf and hi < fin and hf > inicio:
                ocupado = True
                break
        meta_estado = meta_obtener("STAFF_ESTADO", t.get("id")) or {}
        estado_t = (meta_estado.get("estado") or "").upper()
        if ocupado:
            estado_t = "ATENDIENDO"
        elif estado_t not in {"EN_RUTA", "PAUSA", "LIBRE"}:
            estado_t = "LIBRE"
        estados[t.get("id")] = {
            "tecnico_id": t.get("id"),
            "nombre": nombre_persona(empleados.get(t.get("empleado_id"))) or f"Técnico #{t.get('id')}",
            "nivel": t.get("nivel") or "JUNIOR",
            "estado": estado_t,
            "cerca": False,
        }
    return jsonify({"items": list(estados.values())})


@bp.put("/admin/appointments/<int:reserva_id>/technician")
@api_route
@api_auth("ADMINISTRADOR", "SUPERVISOR", "RECEPCIONISTA")
def admin_appointment_assign(reserva_id: int):
    reserva = cita_por_id(reserva_id)
    if not reserva:
        raise ApiError("La cita no existe.", 404, "NO_ENCONTRADO")
    data = cuerpo()
    tecnico_id = entero(data.get("tecnico_id"))
    if tecnico_id <= 0:
        raise ApiError("El técnico es obligatorio.", 400, "DATOS_INVALIDOS", {"tecnico_id": "obligatorio"})
    if not read_one("tecnicos", "id", id=tecnico_id):
        raise ApiError("El técnico no existe.", 404, "NO_ENCONTRADO")
    actual = update_row(
        "reservas",
        {"tecnico_id": tecnico_id, "estado": "ASIGNADA", "fecha_actualizacion": now_utc_iso()},
        id=reserva_id,
    )
    usuario = usuario_por_cliente(reserva.get("cliente_id"))
    if usuario:
        notificar(
            usuario_id=usuario.get("id"),
            cliente_id=reserva.get("cliente_id"),
            tipo="CITA_ASIGNADA",
            titulo="Tu cita fue asignada",
            mensaje=f"La cita CIT-{reserva_id:04d} ya tiene un técnico asignado.",
            referencia=f"/appointments/{reserva_id}",
        )
    datos = appointments_payload([actual or reserva])[0]
    datos.pop("_meta", None)
    return jsonify(datos)


@bp.get("/admin/appointments/route-sheet")
@api_route
@api_auth("ADMINISTRADOR", "SUPERVISOR")
def admin_appointments_route_sheet():
    fecha = texto(request.args.get("fecha"))
    if not fecha:
        raise ApiError("La fecha es obligatoria.", 400, "DATOS_INVALIDOS")
    try:
        date.fromisoformat(fecha)
    except ValueError as exc:
        raise ApiError("La fecha no es válida.", 400, "DATOS_INVALIDOS") from exc
    tecnico_id = arg_entero("tecnico_id", 0, 0)
    reservas = read_rows("reservas", "*", fecha_reserva=fecha, order="hora_inicio")
    if tecnico_id:
        reservas = [r for r in reservas if int(r.get("tecnico_id") or 0) == tecnico_id]
    items = appointments_payload(reservas)
    por_tecnico = defaultdict(list)
    for i, item in enumerate(items):
        if item.get("tecnico"):
            por_tecnico[item["tecnico"]["nombre"]].append(
                {
                    "orden": 0,
                    "hora": item.get("hora_inicio"),
                    "cliente": (item.get("cliente") or {}).get("nombre"),
                    "telefono": (item.get("cliente") or {}).get("telefono"),
                    "direccion": item.get("direccion"),
                    "equipo": " ".join(x for x in ((item.get("equipo") or {}).get("tipo"), (item.get("equipo") or {}).get("marca"), (item.get("equipo") or {}).get("modelo")) if x),
                }
            )
    salida = []
    for tecnico, paradas in por_tecnico.items():
        paradas.sort(key=lambda p: p.get("hora") or "")
        for n, p in enumerate(paradas, 1):
            p["orden"] = n
        salida.append({"tecnico": tecnico, "paradas": paradas})
    return jsonify({"fecha": fecha, "items": salida})


@bp.get("/admin/appointments/export")
@api_route
@api_auth("ADMINISTRADOR", "SUPERVISOR", "RECEPCIONISTA")
def admin_appointments_export():
    formato = texto(request.args.get("formato")).lower()
    if formato not in {"csv", "xlsx"}:
        raise ApiError("El formato debe ser csv o xlsx.", 400, "DATOS_INVALIDOS")
    reservas = read_rows("reservas", "*", order="fecha_reserva", desc=True)
    clientes = _clientes_map()
    filtradas = _filtrar_citas(reservas, clientes)
    items = appointments_payload(filtradas)
    filas = []
    for i in items:
        filas.append(
            [
                i.get("codigo"),
                i.get("fecha"),
                i.get("hora_inicio"),
                i.get("hora_fin"),
                i.get("estado"),
                i.get("tipo_atencion"),
                (i.get("cliente") or {}).get("nombre"),
                (i.get("cliente") or {}).get("telefono"),
                i.get("direccion"),
                " ".join(x for x in ((i.get("equipo") or {}).get("tipo"), (i.get("equipo") or {}).get("marca"), (i.get("equipo") or {}).get("modelo")) if x),
                (i.get("tecnico") or {}).get("nombre"),
            ]
        )
    cabecera = ["Codigo", "Fecha", "Hora inicio", "Hora fin", "Estado", "Tipo", "Cliente", "Telefono", "Direccion", "Equipo", "Tecnico"]
    if formato == "csv":
        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(cabecera)
        writer.writerows(filas)
        contenido = ("\ufeff" + buf.getvalue()).encode("utf-8")
        return Response(
            contenido,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="citas.csv"'},
        )
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Citas"
    ws.append(cabecera)
    for fila in filas:
        ws.append(fila)
    buf = BytesIO()
    wb.save(buf)
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="citas.xlsx"'},
    )


# ---------------------------------------------------------------------------
# Productos E53–E59
# ---------------------------------------------------------------------------

def _puede_ver_costo() -> bool:
    return contexto_actual().get("rol") in {"ADMINISTRADOR", "ALMACENERO"}


@bp.get("/admin/products")
@api_route
@api_auth("ADMINISTRADOR", "ALMACENERO", "VENDEDOR", "SUPERVISOR")
def admin_products_list():
    page, limit, _offset = paginacion(20, 50)
    q = texto(request.args.get("q")).lower()
    categoria_id = arg_entero("categoria_id", 0, 0)
    marca_id = arg_entero("marca_id", 0, 0)
    estado = texto(request.args.get("estado")).upper()
    productos = productos_catalogo(None)
    stocks = stock_info([p.get("id") for p in productos])
    metas = {p.get("id"): meta_producto(p.get("id")) for p in productos}
    conteos = defaultdict(int)
    for p in productos:
        conteos[(p.get("categorias") or {}).get("nombre") or "Sin categoría"] += 1
    filtrados = []
    for p in productos:
        pid = p.get("id")
        payload = _producto_payload(p, stocks, metas, _puede_ver_costo())
        if q and q not in " ".join([payload["sku"], payload["nombre"], str(p.get("modelo") or "")]).lower():
            continue
        if categoria_id and p.get("categoria_id") != categoria_id:
            continue
        if marca_id and p.get("marca_id") != marca_id:
            continue
        if estado and payload["estado"] != estado:
            continue
        filtrados.append({"payload": payload, "id": pid})
    filtrados.sort(key=lambda x: int(x["id"] or 0), reverse=True)
    total = len(filtrados)
    pagina = filtrados[(page - 1) * limit : page * limit]
    return jsonify(
        {
            "items": [x["payload"] for x in pagina],
            "conteos_por_categoria": [{"categoria": k, "total": v} for k, v in sorted(conteos.items())],
            "page": page,
            "limit": limit,
            "total": total,
        }
    )


@bp.get("/admin/products/<int:producto_id>")
@api_route
@api_auth("ADMINISTRADOR", "ALMACENERO", "VENDEDOR", "SUPERVISOR")
def admin_product_detail(producto_id: int):
    return jsonify(_producto_detalle(producto_id, _puede_ver_costo()))


@bp.post("/admin/products")
@api_route
@api_auth("ADMINISTRADOR", "ALMACENERO")
def admin_product_create():
    data = cuerpo()
    sku = requerir(data, "sku").upper()
    nombre = requerir(data, "nombre")
    marca_id = entero(data.get("marca_id"))
    categoria_id = entero(data.get("categoria_id"))
    precio_venta = decimal(data.get("precio_venta"))
    oferta_raw = data.get("precio_oferta")
    precio_oferta = decimal(oferta_raw, 0) if oferta_raw not in (None, "") else None
    costo = decimal(data.get("costo"))
    stock_minimo = entero(data.get("stock_minimo"), -1)
    stock_inicial = entero(data.get("stock_inicial"), -1)
    en_catalogo = booleano(data.get("en_catalogo_web"), True)
    if marca_id <= 0:
        raise ApiError("La marca es obligatoria.", 400, "DATOS_INVALIDOS", {"marca_id": "obligatorio"})
    if categoria_id <= 0:
        raise ApiError("La categoría es obligatoria.", 400, "DATOS_INVALIDOS", {"categoria_id": "obligatorio"})
    if precio_venta <= 0:
        raise ApiError("El precio de venta debe ser mayor que cero.", 400, "DATOS_INVALIDOS", {"precio_venta": "rango"})
    if precio_oferta is not None and precio_oferta >= precio_venta:
        raise ApiError("El precio de oferta debe ser menor al precio de venta.", 400, "DATOS_INVALIDOS", {"precio_oferta": "rango"})
    if costo < 0:
        raise ApiError("El costo no puede ser negativo.", 400, "DATOS_INVALIDOS", {"costo": "rango"})
    if stock_minimo < 0:
        raise ApiError("El stock mínimo es obligatorio.", 400, "DATOS_INVALIDOS", {"stock_minimo": "obligatorio"})
    if stock_inicial < 0:
        raise ApiError("El stock inicial es obligatorio.", 400, "DATOS_INVALIDOS", {"stock_inicial": "obligatorio"})
    if read_one("productos", "id", codigo=sku):
        raise ApiError("Ya existe un producto con ese SKU.", 409, "SKU_DUPLICADO", {"sku": "duplicado"})
    fila = insert_row(
        "productos",
        {
            "codigo": sku,
            "nombre": nombre,
            "descripcion": texto(data.get("descripcion")) or None,
            "tipo_producto": "REPUESTO",
            "marca_id": marca_id,
            "categoria_id": categoria_id,
            "precio_compra": costo,
            "precio_venta": precio_venta,
            "precio_promocional": precio_oferta,
            "activo": bool(en_catalogo),
            "unidad_medida": "UNIDAD",
            "fecha_creacion": now_utc_iso(),
            "fecha_actualizacion": now_utc_iso(),
        },
    )
    pid = int(fila["id"])
    if stock_inicial > 0:
        insert_row(
            "inventario",
            {"producto_id": pid, "stock_actual": stock_inicial, "stock_reservado": 0, "fecha_actualizacion": now_utc_iso()},
        )
    compat = data.get("compatibilidad_marca_ids") if isinstance(data.get("compatibilidad_marca_ids"), list) else []
    meta_guardar(
        "PRODUCTO_META",
        pid,
        {"stock_minimo": stock_minimo, "compatibilidad_marca_ids": [entero(x) for x in compat if entero(x) > 0]},
        usuario_id=contexto_actual().get("id"),
    )
    meta_guardar("PRODUCTO_PRECIO", pid, {"precio": precio_venta, "fecha": now_utc_iso()}, usuario_id=contexto_actual().get("id"))
    _auditar("PRODUCTO_CREADO", nombre, f"SKU {sku}")
    return jsonify(_producto_detalle(pid, _puede_ver_costo())), 201


@bp.patch("/admin/products/<int:producto_id>")
@api_route
@api_auth("ADMINISTRADOR", "ALMACENERO")
def admin_product_update(producto_id: int):
    actual = producto_por_id(producto_id)
    if not actual:
        raise ApiError("El producto no existe.", 404, "NO_ENCONTRADO")
    data = cuerpo()
    cambios = {"fecha_actualizacion": now_utc_iso()}
    if "sku" in data:
        sku = texto(data.get("sku")).upper()
        if not sku:
            raise ApiError("El SKU no puede estar vacío.", 400, "DATOS_INVALIDOS")
        otro = read_one("productos", "id", codigo=sku)
        if otro and int(otro.get("id")) != producto_id:
            raise ApiError("Ya existe un producto con ese SKU.", 409, "SKU_DUPLICADO")
        cambios["codigo"] = sku
    if "nombre" in data:
        cambios["nombre"] = texto(data.get("nombre")) or actual.get("nombre")
    if "descripcion" in data:
        cambios["descripcion"] = texto(data.get("descripcion")) or None
    if "marca_id" in data:
        cambios["marca_id"] = entero(data.get("marca_id"))
    if "categoria_id" in data:
        cambios["categoria_id"] = entero(data.get("categoria_id"))
    if "costo" in data:
        cambios["precio_compra"] = decimal(data.get("costo"))
    if "precio_venta" in data:
        cambios["precio_venta"] = decimal(data.get("precio_venta"))
    if "precio_oferta" in data:
        valor = data.get("precio_oferta")
        cambios["precio_promocional"] = decimal(valor, 0) if valor not in (None, "") else None
    if "en_catalogo_web" in data:
        cambios["activo"] = booleano(data.get("en_catalogo_web"), True)
    precio_venta = decimal(cambios.get("precio_venta", actual.get("precio_venta")))
    oferta = cambios.get("precio_promocional", actual.get("precio_promocional"))
    if precio_venta <= 0:
        raise ApiError("El precio de venta debe ser mayor que cero.", 400, "DATOS_INVALIDOS")
    if oferta not in (None, "") and decimal(oferta) >= precio_venta:
        raise ApiError("El precio de oferta debe ser menor al precio de venta.", 400, "DATOS_INVALIDOS", {"precio_oferta": "rango"})
    update_row("productos", cambios, id=producto_id)
    meta = meta_producto(producto_id)
    if "stock_minimo" in data:
        meta["stock_minimo"] = entero(data.get("stock_minimo"), 10)
    if isinstance(data.get("compatibilidad_marca_ids"), list):
        meta["compatibilidad_marca_ids"] = [entero(x) for x in data["compatibilidad_marca_ids"] if entero(x) > 0]
    if meta:
        meta_guardar("PRODUCTO_META", producto_id, meta, usuario_id=contexto_actual().get("id"))
    if "precio_venta" in data or "precio_oferta" in data:
        meta_guardar("PRODUCTO_PRECIO", producto_id, {"precio": precio_venta, "fecha": now_utc_iso()}, usuario_id=contexto_actual().get("id"))
    _auditar("PRODUCTO_ACTUALIZADO", texto(actual.get("nombre")), f"Producto #{producto_id}")
    return jsonify(_producto_detalle(producto_id, _puede_ver_costo()))


@bp.post("/admin/products/<int:producto_id>/image")
@api_route
@api_auth("ADMINISTRADOR", "ALMACENERO")
def admin_product_image(producto_id: int):
    if not producto_por_id(producto_id):
        raise ApiError("El producto no existe.", 404, "NO_ENCONTRADO")
    archivo = request.files.get("archivo") or request.files.get("imagen") or request.files.get("foto")
    if not archivo or not archivo.filename:
        raise ApiError("Adjunta una imagen en el campo archivo.", 400, "DATOS_INVALIDOS")
    archivo.stream.seek(0, 2)
    tamano = archivo.stream.tell()
    archivo.stream.seek(0)
    if tamano > 2 * 1024 * 1024:
        raise ApiError("La imagen supera el máximo de 2 MB.", 400, "ARCHIVO_MUY_GRANDE")
    try:
        url = storage_service.subir_imagen_producto(archivo, producto_id)
    except Exception as exc:  # noqa: BLE001
        mensaje = getattr(exc, "message", None) or str(exc) or "No se pudo subir la imagen."
        raise ApiError(mensaje, 400, "ARCHIVO_INVALIDO") from exc
    update_row("productos", {"imagen_url": url, "fecha_actualizacion": now_utc_iso()}, id=producto_id)
    return jsonify({"imagen_id": producto_id, "url": url})


@bp.post("/admin/products/<int:producto_id>/stock-movements")
@api_route
@api_auth("ADMINISTRADOR", "ALMACENERO")
def admin_product_stock(producto_id: int):
    if not producto_por_id(producto_id):
        raise ApiError("El producto no existe.", 404, "NO_ENCONTRADO")
    data = cuerpo()
    tipo = texto(data.get("tipo")).upper()
    cantidad = entero(data.get("cantidad"), 0)
    motivo = requerir(data, "motivo")
    if tipo not in {"ENTRADA", "SALIDA", "AJUSTE"}:
        raise ApiError("El tipo debe ser ENTRADA, SALIDA o AJUSTE.", 400, "DATOS_INVALIDOS")
    if tipo != "AJUSTE" and cantidad <= 0:
        raise ApiError("La cantidad debe ser mayor que cero.", 400, "DATOS_INVALIDOS")
    if tipo == "AJUSTE" and cantidad < 0:
        raise ApiError("El stock ajustado no puede ser negativo.", 400, "DATOS_INVALIDOS")
    resultado = mover_stock(producto_id, tipo, cantidad, motivo, contexto_actual().get("id"))
    _auditar("STOCK_MOVIMIENTO", f"Producto #{producto_id}", f"{tipo} {cantidad}: {motivo}")
    return jsonify(resultado)


@bp.delete("/admin/products/<int:producto_id>")
@api_route
@api_auth("ADMINISTRADOR")
def admin_product_delete(producto_id: int):
    if not producto_por_id(producto_id):
        raise ApiError("El producto no existe.", 404, "NO_ENCONTRADO")
    ventas = read_rows("detalle_venta", "id", producto_id=producto_id, limit=1)
    if ventas:
        update_row("productos", {"activo": False, "fecha_actualizacion": now_utc_iso()}, id=producto_id)
    else:
        for inv in read_rows("inventario", "id", producto_id=producto_id):
            from app.api.common import delete_row

            delete_row("inventario", id=inv.get("id"))
        from app.api.common import delete_row

        delete_row("productos", id=producto_id)
    _auditar("PRODUCTO_ELIMINADO", f"Producto #{producto_id}", "Baja lógica" if ventas else "Eliminado")
    return "", 204


# ---------------------------------------------------------------------------
# Usuarios E60–E66
# ---------------------------------------------------------------------------

def _resumen_usuarios(usuarios: list[dict]) -> dict:
    def _n(rol):
        return sum(1 for u in usuarios if (u.get("rol") or "").upper() == rol)

    return {
        "tecnicos": _n("TECNICO"),
        "admins": _n("ADMINISTRADOR"),
        "recepcion": _n("RECEPCIONISTA"),
        "clientes": _n("CLIENTE"),
        "total": len(usuarios),
    }


@bp.get("/admin/users")
@api_route
@api_auth("ADMINISTRADOR")
def admin_users_list():
    page, limit, _offset = paginacion(20, 50)
    usuarios = _usuarios_full()
    rol = texto(request.args.get("rol")).upper()
    estado = texto(request.args.get("estado")).lower()
    q = texto(request.args.get("q")).lower()
    resumen = _resumen_usuarios(usuarios)
    filtrados = []
    for u in usuarios:
        payload = _usuario_payload(u)
        if rol and (payload.get("rol") or "").upper() != rol:
            continue
        if estado in {"activo", "inactivo"} and bool(payload.get("activo")) != (estado == "activo"):
            continue
        if q:
            heno = " ".join(
                [
                    payload.get("nombres") or "",
                    payload.get("apellidos") or "",
                    payload.get("usuario") or "",
                    payload.get("correo") or "",
                    str((_persona_usuario(u)[2] or {}).get("cargo") or ""),
                ]
            ).lower()
            if q not in heno:
                continue
        filtrados.append(payload)
    total = len(filtrados)
    pagina = filtrados[(page - 1) * limit : page * limit]
    return jsonify({"resumen": resumen, "items": pagina, "page": page, "limit": limit, "total": total})


@bp.get("/admin/users/<int:usuario_id>")
@api_route
@api_auth("ADMINISTRADOR")
def admin_user_detail(usuario_id: int):
    usuario = read_one("usuarios", "*", id=usuario_id)
    if not usuario:
        raise ApiError("El usuario no existe.", 404, "NO_ENCONTRADO")
    return jsonify(_usuario_payload(usuario, incluir_detalle=True))


@bp.post("/admin/users")
@api_route
@api_auth("ADMINISTRADOR")
def admin_user_create():
    data = cuerpo()
    nombres = requerir(data, "nombres")
    apellidos = requerir(data, "apellidos")
    tipo_doc = requerir(data, "tipo_documento").upper()
    numero = requerir(data, "numero_documento")
    usuario = requerir(data, "usuario")
    correo = requerir(data, "correo").lower()
    password = texto(data.get("password_temporal"))
    rol = requerir(data, "rol").upper()
    telefono = texto(data.get("telefono"))
    if rol not in ROLES:
        raise ApiError("El rol no es válido.", 400, "DATOS_INVALIDOS")
    if len(password) < 8:
        raise ApiError("La contraseña temporal debe tener al menos 8 caracteres.", 400, "DATOS_INVALIDOS")
    if not 4 <= len(usuario) <= 30:
        raise ApiError("El usuario debe tener entre 4 y 30 caracteres.", 400, "DATOS_INVALIDOS")
    if not correo or "@" not in correo:
        raise ApiError("El correo no es válido.", 400, "DATOS_INVALIDOS")
    doc_id = tipo_documento_id(tipo_doc)
    try:
        if rol == "CLIENTE":
            fila = rest_auth_service.register(
                nombre_usuario=usuario,
                correo=correo,
                password=password,
                rol="CLIENTE",
                nombres=nombres,
                apellidos=apellidos,
                tipo_documento_id=doc_id,
                numero_documento=numero,
                telefono=telefono,
            )
        else:
            fila = rest_auth_service.registrar_trabajador(
                nombre_usuario=usuario,
                correo=correo,
                password=password,
                rol=rol,
                tipo_documento_id=doc_id,
                numero_documento=numero,
                nombres=nombres,
                apellidos=apellidos,
                telefono=telefono,
                cargo=rol.title(),
            )
    except Exception as exc:  # noqa: BLE001
        mensaje = getattr(exc, "message", None) or str(exc) or "No se pudo crear el usuario."
        raise ApiError(mensaje, 400, "DATOS_INVALIDOS") from exc
    nuevo_id = int(fila.get("id"))
    if rol == "TECNICO":
        tecnico = data.get("tecnico") if isinstance(data.get("tecnico"), dict) else {}
        empleado_id = fila.get("empleado_id")
        if empleado_id:
            existente = read_one("tecnicos", "*", empleado_id=empleado_id)
            if existente:
                update_row(
                    "tecnicos",
                    {
                        "especialidad": texto(tecnico.get("especialidad")) or existente.get("especialidad") or "GENERAL",
                        "nivel": texto(tecnico.get("nivel")) or existente.get("nivel") or "JUNIOR",
                    },
                    id=existente.get("id"),
                )
            else:
                insert_row(
                    "tecnicos",
                    {
                        "empleado_id": empleado_id,
                        "especialidad": texto(tecnico.get("especialidad")) or "GENERAL",
                        "nivel": texto(tecnico.get("nivel")) or "JUNIOR",
                    },
                )
    _auditar("USUARIO_CREADO", usuario, f"Rol {rol}")
    return jsonify(_usuario_payload(read_one("usuarios", "*", id=nuevo_id) or fila)), 201


@bp.patch("/admin/users/<int:usuario_id>")
@api_route
@api_auth("ADMINISTRADOR")
def admin_user_update(usuario_id: int):
    usuario = read_one("usuarios", "*", id=usuario_id)
    if not usuario:
        raise ApiError("El usuario no existe.", 404, "NO_ENCONTRADO")
    data = cuerpo()
    rol_nuevo = texto(data.get("rol")).upper()
    if rol_nuevo and rol_nuevo not in ROLES:
        raise ApiError("El rol no es válido.", 400, "DATOS_INVALIDOS")
    tabla, fila_id, persona = _persona_usuario(usuario)
    cambios_persona = {}
    for campo in ("nombres", "apellidos", "telefono"):
        if campo in data:
            cambios_persona[campo] = texto(data.get(campo)) or None
    if "correo" in data:
        correo = texto(data.get("correo")).lower()
        if not correo or "@" not in correo:
            raise ApiError("El correo no es válido.", 400, "DATOS_INVALIDOS")
        cambios_persona["correo"] = correo
    if cambios_persona and tabla and fila_id:
        update_row(tabla, cambios_persona, id=fila_id)
    cambios_usuario = {}
    if "correo" in data:
        cambios_usuario["correo"] = texto(data.get("correo")).lower()
    if rol_nuevo:
        cambios_usuario["rol"] = rol_nuevo
    if cambios_usuario:
        update_row("usuarios", cambios_usuario, id=usuario_id)
    if rol_nuevo == "TECNICO" and usuario.get("empleado_id"):
        tecnico = data.get("tecnico") if isinstance(data.get("tecnico"), dict) else {}
        existente = read_one("tecnicos", "*", empleado_id=usuario.get("empleado_id"))
        if existente:
            update_row(
                "tecnicos",
                {
                    "especialidad": texto(tecnico.get("especialidad")) or existente.get("especialidad") or "GENERAL",
                    "nivel": texto(tecnico.get("nivel")) or existente.get("nivel") or "JUNIOR",
                },
                id=existente.get("id"),
            )
        else:
            insert_row(
                "tecnicos",
                {
                    "empleado_id": usuario.get("empleado_id"),
                    "especialidad": texto(tecnico.get("especialidad")) or "GENERAL",
                    "nivel": texto(tecnico.get("nivel")) or "JUNIOR",
                },
            )
    _auditar("USUARIO_ACTUALIZADO", usuario.get("nombre_usuario") or str(usuario_id), "Datos/rol")
    return jsonify(_usuario_payload(read_one("usuarios", "*", id=usuario_id) or usuario, incluir_detalle=True))


@bp.patch("/admin/users/<int:usuario_id>/status")
@api_route
@api_auth("ADMINISTRADOR")
def admin_user_status(usuario_id: int):
    usuario = read_one("usuarios", "*", id=usuario_id)
    if not usuario:
        raise ApiError("El usuario no existe.", 404, "NO_ENCONTRADO")
    data = cuerpo()
    if "activo" not in data:
        raise ApiError("El campo activo es obligatorio.", 400, "DATOS_INVALIDOS")
    activo = booleano(data.get("activo"))
    update_row("usuarios", {"activo": activo}, id=usuario_id)
    _auditar(
        "USUARIO_ACTIVADO" if activo else "USUARIO_DESACTIVADO",
        usuario.get("nombre_usuario") or str(usuario_id),
        texto(data.get("motivo")),
    )
    return jsonify(_usuario_payload(read_one("usuarios", "*", id=usuario_id) or usuario))


@bp.post("/admin/users/<int:usuario_id>/unblock")
@api_route
@api_auth("ADMINISTRADOR")
def admin_user_unblock(usuario_id: int):
    usuario = read_one("usuarios", "*", id=usuario_id)
    if not usuario:
        raise ApiError("El usuario no existe.", 404, "NO_ENCONTRADO")
    ok = cuenta_guard.desbloquear(
        usuario_id,
        contexto_actual().get("usuario", {}).get("nombre_usuario") or "api",
        usuario.get("nombre_usuario") or "",
    )
    # También se intenta liberar una IP con el mismo id (compatibilidad con E65).
    try:
        update_row("ip_acceso", {"bloqueada": False, "bloqueo_hasta": None}, id=usuario_id)
    except Exception:  # noqa: BLE001
        pass
    _auditar("DESBLOQUEO_USUARIO", usuario.get("nombre_usuario") or str(usuario_id), "Manual")
    return jsonify({"ok": bool(ok)})


@bp.get("/admin/audit-log")
@api_route
@api_auth("ADMINISTRADOR")
def admin_audit_log():
    page, limit, _offset = paginacion(20, 50)
    usuario_id = arg_entero("usuario_id", 0, 0)
    desde = texto(request.args.get("desde"))
    hasta = texto(request.args.get("hasta"))
    eventos = read_rows("seguridad_eventos", "*", order="fecha", desc=True)
    if usuario_id:
        usuario = read_one("usuarios", "nombre_usuario", id=usuario_id) or {}
        nombre = usuario.get("nombre_usuario") or str(usuario_id)
        eventos = [e for e in eventos if nombre.lower() in str(e.get("usuario") or "").lower()]
    for campo, valor in (("desde", desde), ("hasta", hasta)):
        if valor:
            try:
                date.fromisoformat(valor)
            except ValueError as exc:
                raise ApiError("Las fechas no son válidas.", 400, "DATOS_INVALIDOS") from exc
    filtrados = []
    for e in eventos:
        d = _dt(e.get("fecha"))
        if desde and d and d.date() < date.fromisoformat(desde):
            continue
        if hasta and d and d.date() > date.fromisoformat(hasta):
            continue
        filtrados.append(e)
    total = len(filtrados)
    pagina = filtrados[(page - 1) * limit : page * limit]
    items = [
        {
            "fecha": datetime_iso(e.get("fecha")),
            "actor": e.get("admin") or "Sistema",
            "accion": e.get("tipo") or "",
            "objetivo": e.get("usuario") or "",
            "detalle": e.get("detalle") or "",
        }
        for e in pagina
    ]
    return jsonify({"items": items, "page": page, "limit": limit, "total": total})
