"""Ventas y ordenes: listado con filtros y detalle (admin, supervisor, vendedor, recepcionista)."""

from datetime import date, timedelta

from flask import Blueprint, abort, render_template, request

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
