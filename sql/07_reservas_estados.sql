-- 07_reservas_estados.sql
-- Idempotente. Ejecutar ANTES de desplegar el codigo de la Fase 1.

-- Normalizar el estado ASIGNADA (alias historico) a CONFIRMADA.
UPDATE public.reservas
SET estado = 'CONFIRMADA'
WHERE estado = 'ASIGNADA';

-- Columnas para notas del tecnico y motivo de cancelacion (Fase 1).
ALTER TABLE public.reservas
    ADD COLUMN IF NOT EXISTS notas_tecnico text;

ALTER TABLE public.reservas
    ADD COLUMN IF NOT EXISTS motivo_cancelacion text;

-- No se modifica chk_reserva_estado en esta fase: ya acepta ATENDIDA.
