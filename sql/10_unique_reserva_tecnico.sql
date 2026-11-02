-- 10_unique_reserva_tecnico.sql
-- Idempotente.
CREATE UNIQUE INDEX IF NOT EXISTS uq_reservas_tecnico_fecha_hora
    ON public.reservas (tecnico_id, fecha_reserva, hora_inicio)
    WHERE estado <> 'CANCELADA';
