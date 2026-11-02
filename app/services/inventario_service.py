"""Lectura de inventario (fuente de verdad: inventario.stock_actual)."""
from __future__ import annotations

from app.supabase_client import get_reader


def stock_por_producto(producto_ids) -> dict:
    """Devuelve {producto_id: stock_actual_sumado} sumando TODAS las filas.

    Tambien incluye stock_reservado sumado bajo la clave (pid, "reservado").
    """
    ids = [i for i in (producto_ids or []) if i is not None]
    resultado = {pid: 0.0 for pid in ids}
    reservado = {pid: 0.0 for pid in ids}
    if not ids:
        return {"stock": resultado, "reservado": reservado}
    try:
        filas = (
            get_reader()
            .table("inventario")
            .select("producto_id,stock_actual,stock_reservado")
            .in_("producto_id", ids)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001
        return {"stock": resultado, "reservado": reservado}
    for f in filas:
        pid = f.get("producto_id")
        if pid not in resultado:
            resultado[pid] = 0.0
            reservado[pid] = 0.0
        try:
            resultado[pid] += float(f.get("stock_actual") or 0)
        except (TypeError, ValueError):
            pass
        try:
            reservado[pid] += float(f.get("stock_reservado") or 0)
        except (TypeError, ValueError):
            pass
    return {"stock": resultado, "reservado": reservado}
