"""Cierre de caja: arqueo diario y registro de cierres.

El cierre de un día se calcula a partir de los pagos CONFIRMADOS recibidos
ese día (`pagos.fecha_pago`, en hora de Lima). Se guarda en la tabla
`cierres_caja` (ver `sql/03_cierres_caja.sql`) el total esperado en efectivo,
el total por otros medios, lo contado físicamente y la diferencia del arqueo.

El cálculo del día funciona aunque la tabla `cierres_caja` todavía no exista
(solo usa `pagos` y `metodos_pago`); el registro y el listado sí la requieren
y avisan con un mensaje claro si falta ejecutar la migración.
"""

from __future__ import annotations

from datetime import date

from app.exceptions import NotFoundError, ValidationError
from app.services.kpi_service import _desde_ts, _hasta_ts
from app.supabase_client import error_postgrest, get_admin_client, get_reader

POR_PAGINA_CAJA = 10
CODIGO_EFECTIVO = "EFECTIVO"


def _fecha(valor) -> date | None:
    """Convierte 'YYYY-MM-DD' (o un timestamp) en date, o None si no es válido."""
    texto = (str(valor or "")).strip()[:10]
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def _num(valor, defecto: float = 0.0) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return defecto


def calcular_dia(fecha) -> dict:
    """Calcula lo recaudado el día indicado a partir de los pagos confirmados.

    Devuelve el total de ventas del día, cuánto entró en efectivo y por otros
    medios, el número de pagos y el desglose por método de pago.
    """
    dia = _fecha(fecha) or date.today()
    try:
        pagos = (
            get_reader()
            .table("pagos")
            .select("id,monto,metodo_pago_id,estado")
            .eq("estado", "CONFIRMADO")
            .gte("fecha_pago", _desde_ts(dia))
            .lt("fecha_pago", _hasta_ts(dia))
            .limit(1000)
            .execute()
            .data
            or []
        )
        metodos = (
            get_reader().table("metodos_pago").select("id,nombre,codigo").execute().data or []
        )
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc

    nombres = {m["id"]: m.get("nombre") or m.get("codigo") or f"#{m['id']}" for m in metodos}
    codigos = {m["id"]: (m.get("codigo") or "") for m in metodos}

    total_ventas = 0.0
    total_efectivo = 0.0
    desglose: dict = {}
    for p in pagos:
        monto = _num(p.get("monto"))
        total_ventas += monto
        mid = p.get("metodo_pago_id")
        if codigos.get(mid) == CODIGO_EFECTIVO:
            total_efectivo += monto
        fila = desglose.setdefault(
            mid, {"metodo": nombres.get(mid, f"#{mid}"), "monto": 0.0, "n": 0}
        )
        fila["monto"] += monto
        fila["n"] += 1

    total_otros = total_ventas - total_efectivo
    return {
        "fecha": dia.isoformat(),
        "n_pagos": len(pagos),
        "total_ventas": round(total_ventas, 2),
        "total_efectivo": round(total_efectivo, 2),
        "total_otros": round(total_otros, 2),
        "desglose": sorted(
            desglose.values(), key=lambda f: f["monto"], reverse=True
        ),
    }


def listar_cierres(
    desde: date | None = None, hasta: date | None = None,
    pagina: int = 1, por_pagina: int = POR_PAGINA_CAJA,
) -> dict:
    """Lista paginada de cierres registrados, del más reciente al más antiguo."""
    try:
        consulta = get_reader().table("cierres_caja").select("*", count="exact")
        if desde:
            consulta = consulta.gte("fecha", desde.isoformat())
        if hasta:
            consulta = consulta.lte("fecha", hasta.isoformat())
        resp = (
            consulta.order("fecha", desc=True)
            .range((pagina - 1) * por_pagina, pagina * por_pagina - 1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc

    total = resp.count or 0
    paginas = max((total + por_pagina - 1) // por_pagina, 1)
    pagina = min(max(pagina, 1), paginas)
    return {
        "cierres": resp.data or [],
        "total": total,
        "paginas": paginas,
        "pagina": pagina,
    }


def resumen_cierres(desde: date | None = None, hasta: date | None = None) -> dict:
    """Totales del conjunto filtrado (no solo la página): ventas y diferencias."""
    try:
        consulta = get_reader().table("cierres_caja").select(
            "total_ventas,total_efectivo,monto_contado,diferencia,n_pagos"
        )
        if desde:
            consulta = consulta.gte("fecha", desde.isoformat())
        if hasta:
            consulta = consulta.lte("fecha", hasta.isoformat())
        filas = consulta.limit(1000).execute().data or []
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc

    ventas = sum(_num(f.get("total_ventas")) for f in filas)
    efectivo = sum(_num(f.get("total_efectivo")) for f in filas)
    contado = sum(_num(f.get("monto_contado")) for f in filas)
    diferencia = sum(_num(f.get("diferencia")) for f in filas)
    return {
        "n_cierres": len(filas),
        "total_ventas": round(ventas, 2),
        "total_efectivo": round(efectivo, 2),
        "total_contado": round(contado, 2),
        "diferencia": round(diferencia, 2),
    }


def obtener_cierre(cierre_id: int) -> dict:
    try:
        filas = (
            get_reader()
            .table("cierres_caja")
            .select("*")
            .eq("id", cierre_id)
            .limit(1)
            .execute()
            .data
        )
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc
    if not filas:
        raise NotFoundError("El cierre de caja no existe.")
    return filas[0]


def cierre_de_fecha(fecha) -> dict | None:
    """Devuelve el cierre ya registrado para esa fecha, si existe."""
    dia = _fecha(fecha)
    if not dia:
        return None
    try:
        filas = (
            get_reader()
            .table("cierres_caja")
            .select("id,fecha")
            .eq("fecha", dia.isoformat())
            .limit(1)
            .execute()
            .data
        )
    except Exception:  # noqa: BLE001
        return None
    return filas[0] if filas else None


def registrar_cierre(
    fecha, monto_apertura=0, monto_contado=0, observaciones="",
    responsable="", usuario_id=None,
) -> dict:
    """Calcula el día y guarda el cierre en `cierres_caja`.

    La diferencia del arqueo es `monto_contado - (monto_apertura + efectivo)`.
    """
    dia = _fecha(fecha)
    if not dia:
        raise ValidationError("Selecciona una fecha válida para el cierre.")

    if cierre_de_fecha(dia):
        raise ValidationError(f"Ya existe un cierre de caja para el {dia.isoformat()}.")

    dia_calc = calcular_dia(dia)
    apertura = round(_num(monto_apertura), 2)
    contado = round(_num(monto_contado), 2)
    esperado = round(apertura + dia_calc["total_efectivo"], 2)
    diferencia = round(contado - esperado, 2)

    fila = {
        "fecha": dia.isoformat(),
        "monto_apertura": apertura,
        "total_efectivo": dia_calc["total_efectivo"],
        "total_otros": dia_calc["total_otros"],
        "total_ventas": dia_calc["total_ventas"],
        "monto_contado": contado,
        "diferencia": diferencia,
        "n_pagos": dia_calc["n_pagos"],
        "responsable": (responsable or "").strip()[:120] or None,
        "usuario_id": usuario_id,
        "observaciones": (observaciones or "").strip()[:500] or None,
    }
    try:
        resp = get_admin_client().table("cierres_caja").insert(fila).execute()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(error_postgrest(exc)) from exc

    guardado = (resp.data or [fila])[0]
    guardado["esperado"] = esperado
    return guardado
