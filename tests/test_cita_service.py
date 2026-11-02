"""Pruebas basicas de cita_service (sin base de datos real)."""
from app.services.cita_service import (
    TRANSICIONES_CITA,
    _normalizar_estado_cita,
)


def test_normalizar_asignada_a_confirmada():
    assert _normalizar_estado_cita("ASIGNADA") == "CONFIRMADA"


def test_normalizar_completada_a_atendida():
    assert _normalizar_estado_cita("COMPLETADA") == "ATENDIDA"


def test_transiciones_pendiente():
    assert "CONFIRMADA" in TRANSICIONES_CITA["PENDIENTE"]
    assert "CANCELADA" in TRANSICIONES_CITA["PENDIENTE"]


def test_transiciones_atendida_sin_salida():
    assert TRANSICIONES_CITA["ATENDIDA"] == set()
