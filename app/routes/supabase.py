"""Ruta de verificación del estado de Supabase."""

from flask import Blueprint, jsonify

from app.supabase_client import is_configured, test_connection

bp = Blueprint("supabase", __name__)


@bp.get("/supabase/status")
def status():
    """GET /supabase/status — muestra si Supabase está configurado y accesible."""
    reporte = test_connection()
    return jsonify(reporte), (200 if reporte.get("ok") else 502)


@bp.get("/supabase/health")
def health():
    """GET /supabase/health — rápido: solo confirma configuración y reachability."""
    return jsonify({"configurado": is_configured()}), 200