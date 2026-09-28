"""KPIs del panel administrativo.

Los seis indicadores se calculan aqui, en el servidor, leyendo las tablas
reales de Supabase con el cliente REST (`app.supabase_client`). El frontend
solo pinta el resultado: no hace ningun calculo de negocio.

No se crean tablas, vistas ni funciones nuevas en la base de datos (no hay
migraciones). Todo lo que el panel necesita ya existe:

    reservas.fecha_primera_atencion   -> KPI-01 / KPI-02
    visitas_web.sesion_id              -> KPI-03 (misma clave que carritos)
    productos.activo + inventario      -> KPI-04
    carritos + detalle_carrito + ventas-> KPI-05
    notificaciones.estado_envio        -> KPI-06

Mapeos con el esquema real (ver README de KPIs):
  * `ventas.canal_venta` se guarda como 'TIENDA' (no 'web'); se acepta tambien
    'WEB' por si el canal cambia mas adelante.
  * No existe estado de carrito 'CONVERTIDO': tras el pago el carrito queda en
    'ABANDONADO' y `carrito_service.vaciar()` borra su detalle. Por eso un
    carrito se considera convertido si tiene una venta asociada, no por su
    estado.
  * `reservas.estado` no trae los estados de cancelacion del informe; se
    excluyen igual, en mayusculas, por si aparecen.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.config import Config
from app.supabase_client import error_postgrest, get_reader

log = logging.getLogger(__name__)

UTC = timezone.utc
MESES_ES = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "set", "oct", "nov", "dic",
]

# Ventanas de la ficha tecnica: el panel muestra el anio natural en curso.
VENTANA_ANIOS = 1
VENTANA_MESES = 6

#: Metas por KPI. El valor sale de Config (.env), aqui solo el rotulo.
METAS = {
    "KPI-01": ("Tiempo medio de primera atención", "horas"),
    "KPI-02": ("Atención dentro de 24 h", "%"),
    "KPI-03": ("Conversión de la tienda web", "%"),
    "KPI-04": ("Disponibilidad de repuestos", "%"),
    "KPI-05": ("Abandono de carrito", "%"),
    "KPI-06": ("Entrega de notificaciones", "%"),
}

#: Subtitulo de cada tarjeta: de donde sale exactamente el numero.
SUBTITULOS = {
    "KPI-01": "promedio de horas hasta la primera atención",
    "KPI-02": "citas respondidas dentro de 24 horas",
    "KPI-03": "sesiones que visitaron y compraron",
    "KPI-04": "productos activos con stock",
    "KPI-05": "carritos con productos que no se vendieron",
    "KPI-06": "notificaciones entregadas sobre el total",
}

#: Tooltips: que mide, como se calcula y que supuesto se aplico.
AYUDA = {
    "KPI-01": (
        "Horas entre que se creo la cita y su primera atencion (reserva que "
        "tiene fecha_primera_atencion), en promedio. Se excluyen las citas "
        "canceladas. Meta: 24 h o menos."
    ),
    "KPI-02": (
        "Porcentaje de citas cuya primera atencion llego dentro de las 24 h. "
        "En el denominador entran las citas ya atendidas y las que llevan mas "
        "de 24 h sin atencion (cuentan como incumplimiento)."
    ),
    "KPI-03": (
        "Sesiones que visitaron la web y compraron en el mismo periodo. Se "
        "cuenta cada sesion una vez: la visita y la venta se vinculan por la "
        "misma clave de sesion (user:<id> o anon:<token>). No cuentan las "
        "visitas del administrador, que gestiona y no compra."
    ),
    "KPI-04": (
        "Foto del catalogo: productos activos que tienen stock mayor que 0. "
        "Es una foto del momento (el inventario no guarda historico), por eso "
        "el grafico usa el snapshot semanal guardado por el comando "
        "kpi:snapshot."
    ),
    "KPI-05": (
        "Carritos con productos que nunca se convirtieron sobre los carritos "
        "con productos ya resueltos. Son abandonados los que quedaron "
        "'ABANDONADO'/'EXPIRADO' o cuya vigencia vencio y no llegaron a "
        "generar una venta. Los carritos todavia activos no cuentan."
    ),
    "KPI-06": (
        "Notificaciones enviadas o entregadas sobre el total enviado, "
        "entregado o fallido. La ficha muestra los codigos de error mas "
        "frecuentes para saber que corregir."
    ),
}


class KpiError(Exception):
    """Fallo al calcular los KPIs (no se pudo leer Supabase)."""


# ---------------------------------------------------------------------------
# Fechas
# ---------------------------------------------------------------------------

def _iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _desde_ts(d: date) -> str:
    """Limite inferior inclusivo (UTC) para filtrar por fecha."""
    return f"{_iso(d)}T00:00:00+00:00"


def _hasta_ts(d: date) -> str:
    """Limite superior EXCLUSIVO (UTC) para filtrar por fecha."""
    return f"{_iso(d + timedelta(days=1))}T00:00:00+00:00"


def _dt(valor) -> datetime | None:
    """Convierte lo que devuelve PostgREST a datetime con zona horaria."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    try:
        d = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def parsear_periodo(desde_txt, hasta_txt) -> tuple[date, date, str | None]:
    """Valida el rango pedido. Devuelve (desde, hasta, error)."""
    hoy = datetime.now(UTC).date()
    if not desde_txt or not hasta_txt:
        return hoy.replace(day=1), hoy, None
    try:
        d0 = date.fromisoformat(str(desde_txt).strip()[:10])
        d1 = date.fromisoformat(str(hasta_txt).strip()[:10])
    except ValueError:
        return hoy.replace(day=1), hoy, "Las fechas no son validas."
    if d1 < d0:
        return hoy.replace(day=1), hoy, "La fecha final es anterior a la inicial."
    if (d1 - d0).days > 1100:
        return hoy.replace(day=1), hoy, "El rango no puede superar 3 anos."
    return d0, d1, None


def rango_por_defecto() -> tuple[date, date]:
    """Mes en curso hasta hoy."""
    hoy = datetime.now(UTC).date()
    return hoy.replace(day=1), hoy


def ventanas_por_defecto() -> list[dict]:
    """Periodos sugeridos en los filtros."""
    hoy = datetime.now(UTC).date()
    return [
        {"clave": "mes", "etiqueta": "Mes actual",
         "desde": _iso(hoy.replace(day=1)), "hasta": _iso(hoy)},
        {"clave": "mes_anterior", "etiqueta": "Mes anterior",
         "desde": _iso((hoy.replace(day=1) - timedelta(days=1)).replace(day=1)),
         "hasta": _iso(hoy.replace(day=1) - timedelta(days=1))},
        {"clave": "trimestre", "etiqueta": "Ultimos 3 meses",
         "desde": _iso((hoy - timedelta(days=90)).replace(day=1)), "hasta": _iso(hoy)},
        {"clave": "semana", "etiqueta": "Ultimos 7 dias",
         "desde": _iso(hoy - timedelta(days=6)), "hasta": _iso(hoy)},
        {"clave": "anio", "etiqueta": "Ano en curso",
         "desde": _iso(hoy.replace(month=1, day=1)), "hasta": _iso(hoy)},
    ]


def _meses(desde: date, hasta: date, limite: int = 12) -> list[tuple[str, date, date]]:
    """Buckets mensuales que tocan el rango, del mas reciente al mas antiguo."""
    buckets = []
    y, m = desde.year, desde.month
    while True:
        primero = date(y, m, 1)
        ultimo = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
        if primero > hasta:
            break
        if ultimo >= desde:
            buckets.append((primero.strftime("%Y-%m"), primero, ultimo))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return list(reversed(buckets[-limite:]))


def _etiqueta_mes(ym: str) -> str:
    y, m = ym.split("-")
    return f"{MESES_ES[int(m) - 1]} {y[2:]}"


def _etiqueta_dia(iso_txt: str) -> str:
    d = date.fromisoformat(iso_txt)
    return f"{d.day} {MESES_ES[d.month - 1]}"


# ---------------------------------------------------------------------------
# Lectura desde Supabase
# ---------------------------------------------------------------------------

def _consulta(builder, tabla: str):
    """Ejecuta una consulta. Devuelve las filas o None si fallo (se registra)."""
    try:
        return builder.execute().data or []
    except Exception as exc:  # noqa: BLE001
        log.exception("KPIs: fallo al leer %s", tabla)
        raise KpiError(f"No se pudo leer «{tabla}» en Supabase: {error_postgrest(exc)}") from exc


def _reservas(desde: date, hasta: date) -> list[dict]:
    return _consulta(
        get_reader()
        .table("reservas")
        .select("id,estado,fecha_creacion,fecha_primera_atencion")
        .gte("fecha_creacion", _desde_ts(desde))
        .lt("fecha_creacion", _hasta_ts(hasta)),
        "reservas",
    )


def _carritos(desde: date, hasta: date, extra_ids: list) -> list[dict]:
    q = (
        get_reader()
        .table("carritos")
        .select("id,estado,fecha_creacion,fecha_expiracion,usuario_sesion")
        .gte("fecha_creacion", _desde_ts(desde))
        .lt("fecha_creacion", _hasta_ts(hasta))
    )
    filas = _consulta(q, "carritos")
    vistos = {f["id"] for f in filas}
    faltan = [i for i in extra_ids if i and i not in vistos]
    if faltan:
        extra = _consulta(
            get_reader()
            .table("carritos")
            .select("id,estado,fecha_creacion,fecha_expiracion,usuario_sesion")
            .in_("id", faltan),
            "carritos",
        )
        filas.extend(extra)
    return filas


def _ventas(desde: date, hasta: date) -> list[dict]:
    return _consulta(
        get_reader()
        .table("ventas")
        .select("id,carrito_id,estado,canal_venta,fecha_venta")
        .gte("fecha_venta", _desde_ts(desde))
        .lt("fecha_venta", _hasta_ts(hasta)),
        "ventas",
    )


def _ids_con_detalle(carrito_ids: list) -> set:
    """Carritos que alguna vez tuvieron lineas en detalle_carrito."""
    if not carrito_ids:
        return set()
    filas = _consulta(
        get_reader().table("detalle_carrito").select("carrito_id").in_("carrito_id", carrito_ids),
        "detalle_carrito",
    )
    return {f["carrito_id"] for f in filas if f.get("carrito_id")}


def _visitas(desde: date, hasta: date) -> list[dict]:
    return _consulta(
        get_reader()
        .table("visitas_web")
        .select("sesion_id,fecha_visita,ruta")
        .gte("fecha_visita", _desde_ts(desde))
        .lt("fecha_visita", _hasta_ts(hasta)),
        "visitas_web",
    )


def _notificaciones(desde: date, hasta: date) -> list[dict]:
    return _consulta(
        get_reader()
        .table("notificaciones")
        .select("estado_envio,codigo_error,fecha_creacion")
        .gte("fecha_creacion", _desde_ts(desde))
        .lt("fecha_creacion", _hasta_ts(hasta)),
        "notificaciones",
    )


def _catalogo() -> list[dict]:
    """Productos activos con su stock real (LEFT JOIN de inventario)."""
    productos = _consulta(
        get_reader().table("productos").select("id,nombre,activo"),
        "productos",
    )
    filas_inv = _consulta(
        get_reader().table("inventario").select("producto_id,stock_actual"),
        "inventario",
    )
    stock: dict = {}
    for f in filas_inv:
        pid = f.get("producto_id")
        if pid is None:
            continue
        try:
            stock[pid] = stock.get(pid, 0) + float(f.get("stock_actual") or 0)
        except (TypeError, ValueError):
            stock[pid] = stock.get(pid, 0)
    activos = [p for p in productos if p.get("activo")]
    return [
        {
            "id": p.get("id"),
            "nombre": p.get("nombre") or "(sin nombre)",
            "stock": float(stock.get(p.get("id"), 0) or 0),
        }
        for p in activos
    ]


# ---------------------------------------------------------------------------
# Utilidades de calculo
# ---------------------------------------------------------------------------

def _en(fecha, desde: date, hasta: date) -> bool:
    d = _dt(fecha)
    return d is not None and _desde_ts(desde) <= d.isoformat() < _hasta_ts(hasta)


def _mes_de(fecha, buckets) -> str | None:
    d = _dt(fecha)
    if d is None:
        return None
    dia = d.date()
    for ym, ini, fin in buckets:
        if ini <= dia <= fin:
            return ym
    return None


def _pct(num, den) -> float | None:
    return round(num / den * 100, 2) if den else None


def _no_cancelada(estado) -> bool:
    return (estado or "").strip().upper() not in ("CANCELADA", "CANCELADO", "ANULADA")


def _venta_valida(v: dict) -> bool:
    """Venta que cuenta como compra: no anulada y de canal web/tienda."""
    return _no_cancelada(v.get("estado")) and (v.get("canal_venta") or "").strip().upper() in (
        "TIENDA",
        "WEB",
    )


def _estado_meta(valor: float | None, meta: float, menor_es_mejor: bool) -> str:
    """Semaforo: ok / cerca / critico / sin_datos."""
    if valor is None:
        return "sin_datos"
    tol = max(1.0, meta * Config.KPI_TOLERANCIA_PCT / 100)
    cumple = valor <= meta if menor_es_mejor else valor >= meta
    if cumple:
        return "ok"
    cerca = valor <= meta + tol if menor_es_mejor else valor >= meta - tol
    return "cerca" if cerca else "critico"


def _variacion(actual: float | None, previo: float | None, menor_es_mejor: bool) -> dict:
    if actual is None or previo is None:
        return {"texto": "—", "clase": "trend-flat", "delta": None}
    delta = round(actual - previo, 2)
    if abs(delta) < 0.005:
        return {"texto": "igual que el periodo anterior", "clase": "trend-flat", "delta": 0.0}
    mejor = delta < 0 if menor_es_mejor else delta > 0
    return {
        "texto": f"{'+' if delta > 0 else ''}{delta:g} vs. periodo anterior",
        "clase": "trend-up" if mejor else "trend-down",
        "delta": delta,
    }


def _serie(buckets, valores: dict) -> list[dict]:
    """Serie mensual en orden cronologico; los meses sin datos van en null."""
    return [
        {
            "periodo": ym,
            "etiqueta": _etiqueta_mes(ym),
            "valor": valores.get(ym),
        }
        for ym, _ini, _fin in buckets
    ]


def _distribucion(horas: list[float]) -> list[dict]:
    """Reparte las horas de primera atencion en tramos (grafico de barras)."""
    tramos = [
        ("0-4 h", 0, 4, "ok"),
        ("4-12 h", 4, 12, "ok"),
        ("12-24 h", 12, 24, "cerca"),
        ("24-48 h", 24, 48, "critico"),
        ("48 h o mas", 48, None, "critico"),
    ]
    salida = []
    for etiqueta, ini, fin, estado in tramos:
        total = sum(
            1
            for h in horas
            if h >= ini and (fin is None or h < fin)
        )
        salida.append({"etiqueta": etiqueta, "total": total, "estado": estado})
    return salida


# ---------------------------------------------------------------------------
# KPI-01 / KPI-02  (primera atencion de las citas)
# ---------------------------------------------------------------------------

def _horas_atencion(filas: list[dict]) -> list[dict | None]:
    """[{'horas': float|None, 'fecha_creacion': datetime, 'mes': ym}] o None."""
    salida = []
    for f in filas:
        if not _no_cancelada(f.get("estado")):
            salida.append(None)
            continue
        fc = _dt(f.get("fecha_creacion"))
        fpa = _dt(f.get("fecha_primera_atencion"))
        if fc is None:
            salida.append(None)
            continue
        horas = None
        if fpa is not None and fpa >= fc:
            horas = round((fpa - fc).total_seconds() / 3600, 4)
        salida.append({"horas": horas, "fecha_creacion": fc, "mes": None})
    return salida


def _kpi01(items, desde, hasta, buckets) -> dict:
    validos = [i for i in items if i]
    atendidos = [i for i in validos if i["horas"] is not None]
    en_rango = [i for i in atendidos if _en(i["fecha_creacion"], desde, hasta)]
    valor = round(sum(i["horas"] for i in en_rango) / len(en_rango), 2) if en_rango else None
    por_mes = {}
    for i in en_rango:
        ym = _mes_de(i["fecha_creacion"], buckets)
        if ym:
            por_mes.setdefault(ym, []).append(i["horas"])
    serie = {
        ym: round(sum(v) / len(v), 2) for ym, v in por_mes.items() if v
    }
    return {
        "valor": valor,
        "numerador": None,
        "denominador": len(en_rango),
        "serie": _serie(buckets, serie),
        "detalle": {
            "citas": len(en_rango),
            "min": round(min(i["horas"] for i in en_rango), 2) if en_rango else None,
            "max": round(max(i["horas"] for i in en_rango), 2) if en_rango else None,
            "distribucion": _distribucion([i["horas"] for i in en_rango]),
        },
    }


def _kpi02(items, desde, hasta, buckets, ahora) -> dict:
    validos = [i for i in items if i]
    limite = ahora - timedelta(hours=24)

    def _cuenta(subset):
        # Denominador: citas ya atendidas (lleguen tarde o temprano) mas las que
        # llevan mas de 24 h sin atencion, que cuentan como incumplimiento.
        atendidas = [i for i in subset if i["horas"] is not None]
        dentro = [i for i in atendidas if i["horas"] <= 24]
        vencidas = [
            i for i in subset if i["horas"] is None and i["fecha_creacion"] < limite
        ]
        return len(dentro), len(atendidas) + len(vencidas)

    num, den = _cuenta([i for i in validos if _en(i["fecha_creacion"], desde, hasta)])
    por_mes = {}
    for i in validos:
        ym = _mes_de(i["fecha_creacion"], buckets)
        if ym:
            por_mes.setdefault(ym, []).append(i)
    serie = {ym: _pct(*_cuenta(v)) for ym, v in por_mes.items()}
    return {
        "valor": _pct(num, den),
        "numerador": num,
        "denominador": den,
        "serie": _serie(buckets, serie),
        "detalle": {
            "dentro_24h": num,
            "fuera_24h": den - num,
        },
    }


# ---------------------------------------------------------------------------
# KPI-03  (visitas web -> compras)
# ---------------------------------------------------------------------------

def _kpi03(visitas, ventas, carritos, desde, hasta, buckets) -> dict:
    sesion_carrito = {c["id"]: c.get("usuario_sesion") for c in carritos}
    compradoras = {
        sesion_carrito.get(v.get("carrito_id"))
        for v in ventas
        if _venta_valida(v) and sesion_carrito.get(v.get("carrito_id"))
    }

    # Sesion + mes de cada visita (una sesion puede repetirse en el periodo).
    sesiones: dict = {}
    for v in visitas:
        if not v.get("sesion_id"):
            continue
        ym = _mes_de(v.get("fecha_visita"), buckets)
        sesiones.setdefault((v["sesion_id"], ym), 0)
        sesiones[(v["sesion_id"], ym)] += 1

    por_mes: dict = {}
    for (sesion, ym) in sesiones:
        if not ym:
            continue
        visitas_ym, compras_ym = por_mes.setdefault(ym, (0, 0))
        por_mes[ym] = (visitas_ym + 1, compras_ym + (1 if sesion in compradoras else 0))
    serie = {ym: _pct(c, v) for ym, (v, c) in por_mes.items()}

    # Valor del periodo: sesiones que visitaron y compraron.
    en_rango = [v for v in visitas if _en(v.get("fecha_visita"), desde, hasta)]
    sesiones_rango = {v["sesion_id"] for v in en_rango if v.get("sesion_id")}
    carritos_rango = {
        c.get("usuario_sesion")
        for c in carritos
        if _en(c.get("fecha_creacion"), desde, hasta) and c.get("usuario_sesion")
    }
    conversiones = sesiones_rango & compradoras
    return {
        "valor": _pct(len(conversiones), len(sesiones_rango)),
        "numerador": len(conversiones),
        "denominador": len(sesiones_rango),
        "serie": _serie(buckets, serie),
        "detalle": {
            "visitas": len(sesiones_rango),
            "carritos": len(carritos_rango & sesiones_rango),
            "compras": len(conversiones),
        },
    }


# ---------------------------------------------------------------------------
# KPI-04  (disponibilidad del catalogo)
# ---------------------------------------------------------------------------

def _ruta_snapshots() -> Path:
    return Path(__file__).resolve().parents[2] / Config.KPI_SNAPSHOT_FILE


def leer_snapshots() -> list[dict]:
    """Snapshot semanal de disponibilidad (archivo JSONL, uno por semana)."""
    ruta = _ruta_snapshots()
    if not ruta.exists():
        return []
    salida: dict[str, dict] = {}
    try:
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea:
                continue
            try:
                fila = json.loads(linea)
            except json.JSONDecodeError:
                continue
            periodo = fila.get("periodo")
            if periodo and fila.get("valor") is not None:
                salida[periodo] = fila
    except OSError as exc:
        log.warning("KPIs: no se pudo leer el historico de snapshots: %s", exc)
        return []
    return [salida[k] for k in sorted(salida)]


def guardar_snapshot(catalogo: list[dict]) -> dict:
    """Guarda (o reemplaza) la foto de disponibilidad de la semana en curso."""
    con_stock = [p for p in catalogo if p["stock"] > 0]
    total = len(catalogo)
    valor = _pct(len(con_stock), total)
    hoy = datetime.now(UTC).date()
    lunes = hoy - timedelta(days=hoy.weekday())
    fila = {
        "periodo": _iso(lunes),
        "valor": valor,
        "numerador": len(con_stock),
        "denominador": total,
        "calculado_en": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    ruta = _ruta_snapshots()
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        actuales = [s for s in leer_snapshots() if s.get("periodo") != fila["periodo"]]
        with ruta.open("w", encoding="utf-8") as fh:
            for s in actuales:
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")
            fh.write(json.dumps(fila, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise KpiError(f"No se pudo guardar el snapshot semanal: {exc}") from exc
    return fila


def _kpi04_previo(desde: date) -> dict:
    """Valor de KPI-04 en el snapshot semanal anterior al periodo."""
    antes = [s for s in leer_snapshots() if str(s.get("periodo")) < _iso(desde)]
    if not antes:
        return {"valor": None}
    return {"valor": round(float(antes[-1]["valor"]), 2)}


def _kpi04(catalogo: list[dict], desde, hasta) -> dict:
    con_stock = [p for p in catalogo if p["stock"] > 0]
    total = len(catalogo)
    valor = _pct(len(con_stock), total)
    desde_iso, hasta_iso = _iso(desde), _iso(hasta)
    snapshots = [s for s in leer_snapshots() if desde_iso <= str(s.get("periodo")) <= hasta_iso]
    return {
        "valor": valor,
        "numerador": len(con_stock),
        "denominador": total,
        "serie": [
            {
                "periodo": s["periodo"],
                "etiqueta": _etiqueta_dia(s["periodo"]),
                "valor": round(float(s["valor"]), 2),
            }
            for s in snapshots
        ],
        "detalle": {
            "sin_stock": [
                {"nombre": p["nombre"], "stock": p["stock"]}
                for p in catalogo
                if p["stock"] <= 0
            ][:20],
            "ultimo_snapshot": snapshots[-1]["periodo"] if snapshots else None,
        },
    }


# ---------------------------------------------------------------------------
# KPI-05  (abandono de carrito)
# ---------------------------------------------------------------------------

def _kpi05(carritos, ventas, con_detalle, desde, hasta, buckets, ahora) -> dict:
    con_venta = {v.get("carrito_id") for v in ventas if _venta_valida(v)}
    filas = [c for c in carritos if _en(c.get("fecha_creacion"), desde, hasta)]

    def _clasificar(c) -> str:
        if c.get("id") in con_venta:
            return "convertido"
        if c.get("id") not in con_detalle:
            return "vacio"
        estado = (c.get("estado") or "").strip().upper()
        venc = _dt(c.get("fecha_expiracion"))
        if estado in ("ABANDONADO", "EXPIRADO") or (venc and venc <= ahora):
            return "abandonado"
        return "activo"

    base = {c["id"]: _clasificar(c) for c in filas}
    convertidos = sum(1 for v in base.values() if v == "convertido")
    abandonados = sum(1 for v in base.values() if v == "abandonado")
    en_curso = sum(1 for v in base.values() if v == "activo")
    resueltos = convertidos + abandonados
    valor = _pct(abandonados, resueltos)

    por_mes: dict = {}
    for c in filas:
        ym = _mes_de(c.get("fecha_creacion"), buckets)
        if ym:
            por_mes.setdefault(ym, []).append(base[c["id"]])
    serie = {}
    for ym, estados in por_mes.items():
        ab = sum(1 for e in estados if e == "abandonado")
        cv = sum(1 for e in estados if e == "convertido")
        serie[ym] = _pct(ab, ab + cv)
    return {
        "valor": valor,
        "numerador": abandonados,
        "denominador": resueltos,
        "serie": _serie(buckets, serie),
        "detalle": {
            "abandonados": abandonados,
            "convertidos": convertidos,
            "en_curso": en_curso,
        },
    }


# ---------------------------------------------------------------------------
# KPI-06  (entrega de notificaciones)
# ---------------------------------------------------------------------------

def _kpi06(notis, desde, hasta, buckets) -> dict:
    ok = [n for n in notis if (n.get("estado_envio") or "").strip().lower() in ("enviado", "entregado")]
    fallidas = [n for n in notis if (n.get("estado_envio") or "").strip().lower() == "fallido"]
    valor = _pct(len(ok), len(ok) + len(fallidas))

    errores: dict = {}
    for n in fallidas:
        clave = (n.get("codigo_error") or "SIN_CODIGO").strip().upper()
        errores[clave] = errores.get(clave, 0) + 1
    top = sorted(errores.items(), key=lambda kv: kv[1], reverse=True)[:5]

    por_mes: dict = {}
    for n in notis:
        ym = _mes_de(n.get("fecha_creacion"), buckets)
        if ym:
            por_mes.setdefault(ym, []).append(n)
    serie = {}
    for ym, grupo in por_mes.items():
        b = sum(1 for n in grupo if (n.get("estado_envio") or "").strip().lower() in ("enviado", "entregado"))
        m = sum(1 for n in grupo if (n.get("estado_envio") or "").strip().lower() == "fallido")
        serie[ym] = _pct(b, b + m)
    return {
        "valor": valor,
        "numerador": len(ok),
        "denominador": len(ok) + len(fallidas),
        "serie": _serie(buckets, serie),
        "detalle": {
            "entregadas": sum(1 for n in ok if (n.get("estado_envio") or "").lower() == "entregado"),
            "enviadas": sum(1 for n in ok if (n.get("estado_envio") or "").lower() == "enviado"),
            "fallidas": len(fallidas),
            "errores": [{"codigo": c, "total": t} for c, t in top],
        },
    }


# ---------------------------------------------------------------------------
# Armado del resumen
# ---------------------------------------------------------------------------

def meta_de(codigo: str) -> float:
    """Meta configurada de un KPI (viene de Config/.env)."""
    return float(
        {
            "KPI-01": Config.KPI_META_01_HORAS,
            "KPI-02": Config.KPI_META_02_PCT,
            "KPI-03": Config.KPI_META_03_PCT,
            "KPI-04": Config.KPI_META_04_PCT,
            "KPI-05": Config.KPI_META_05_PCT,
            "KPI-06": Config.KPI_META_06_PCT,
        }[codigo]
    )


def calcular(desde: date, hasta: date) -> dict:
    """Los 6 KPIs del rango [desde, hasta] mas el periodo anterior comparable."""
    dias = (hasta - desde).days + 1
    p_desde = desde - timedelta(days=dias)
    p_hasta = desde - timedelta(days=1)
    ahora = datetime.now(UTC)
    buckets = _meses(desde, hasta)
    buckets_prev = _meses(p_desde, p_hasta)
    ext_desde = min(desde, p_desde)

    # Una sola lectura por tabla cubre el periodo actual y el anterior.
    reservas = _reservas(ext_desde, hasta)
    ventas = _ventas(ext_desde, hasta)
    carritos = _carritos(ext_desde, hasta, [v.get("carrito_id") for v in ventas])
    con_detalle = _ids_con_detalle([c["id"] for c in carritos])
    visitas = _visitas(ext_desde, hasta)
    notis = _notificaciones(ext_desde, hasta)
    catalogo = _catalogo()

    items = _horas_atencion(reservas)
    calculos = {
        "KPI-01": lambda d, h, b: _kpi01(items, d, h, b),
        "KPI-02": lambda d, h, b: _kpi02(items, d, h, b, ahora),
        "KPI-03": lambda d, h, b: _kpi03(visitas, ventas, carritos, d, h, b),
        "KPI-05": lambda d, h, b: _kpi05(carritos, ventas, con_detalle, d, h, b, ahora),
        "KPI-06": lambda d, h, b: _kpi06(notis, d, h, b),
    }

    kpis = []
    for codigo, (nombre, unidad) in METAS.items():
        meta = meta_de(codigo)
        menor = codigo == "KPI-01" or codigo == "KPI-05"
        base = {
            "codigo": codigo,
            "nombre": nombre,
            "unidad": unidad,
            "meta": meta,
            "menor_es_mejor": menor,
            "ayuda": AYUDA[codigo],
            "subtitulo": SUBTITULOS[codigo],
        }
        try:
            if codigo == "KPI-04":
                actual = _kpi04(catalogo, desde, hasta)
                previo = _kpi04_previo(desde)
            else:
                actual = calculos[codigo](desde, hasta, buckets)
                previo = calculos[codigo](p_desde, p_hasta, buckets_prev)
        except KpiError as exc:
            base.update({"error": str(exc)})
            kpis.append(base)
            continue

        valor = actual["valor"]
        base.update(
            {
                "valor": valor,
                "valor_previo": previo["valor"],
                "numerador": actual["numerador"],
                "denominador": actual["denominador"],
                "serie": actual["serie"],
                "detalle": actual["detalle"],
                "cumple": (valor is not None and (valor <= meta if menor else valor >= meta)),
                "estado": _estado_meta(valor, meta, menor),
                "variacion": _variacion(valor, previo["valor"], menor),
            }
        )
        kpis.append(base)

    cumplimiento = [k for k in kpis if k.get("valor") is not None and not k.get("error")]
    en_meta = sum(1 for k in cumplimiento if k["cumple"])
    return {
        "desde": _iso(desde),
        "hasta": _iso(hasta),
        "dias": dias,
        "periodo_anterior": {"desde": _iso(p_desde), "hasta": _iso(p_hasta)},
        "generado_en": ahora.isoformat(timespec="seconds"),
        "resumen": {
            "total": len(cumplimiento),
            "en_meta": en_meta,
            "pct": _pct(en_meta, len(cumplimiento)),
            "criticos": [k["codigo"] for k in cumplimiento if not k["cumple"]],
        },
        "kpis": kpis,
    }


def csv(resumen: dict) -> str:
    """Resumen plano para descargar (compatible con Excel en es-ES)."""
    lineas = [
        "KPI;Indicador;Valor;Meta;Unidad;Periodo;Numerador;Denominador;Estado;Variacion"
    ]
    fmt = {"horas": "{:g} h", "%": "{:g} %"}
    for k in resumen.get("kpis", []):
        if k.get("error"):
            lineas.append(
                f"{k['codigo']};{k['nombre']};ERROR;;;{resumen['desde']} a {resumen['hasta']};;;;"
                f"{k['error']}"
            )
            continue
        unidad = fmt.get(k["unidad"], "{:g}")
        valor = unidad.format(k["valor"]) if k["valor"] is not None else "Sin datos"
        meta = unidad.format(k["meta"])
        estado = {
            "ok": "Cumple",
            "cerca": "Cerca de la meta",
            "critico": "No cumple",
            "sin_datos": "Sin datos",
        }[k["estado"]]
        lineas.append(
            ";".join(
                [
                    k["codigo"],
                    k["nombre"],
                    valor,
                    meta,
                    k["unidad"],
                    f"{resumen['desde']} a {resumen['hasta']}",
                    str(k["numerador"] if k["numerador"] is not None else ""),
                    str(k["denominador"] if k["denominador"] is not None else ""),
                    estado,
                    k["variacion"]["texto"],
                ]
            )
        )
    return "\r\n".join(lineas)
