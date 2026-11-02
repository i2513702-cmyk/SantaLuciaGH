-- 09_reserva_historial.sql
-- Idempotente.

CREATE TABLE IF NOT EXISTS public.reserva_historial (
    id bigserial PRIMARY KEY,
    reserva_id bigint NOT NULL REFERENCES public.reservas(id) ON DELETE CASCADE,
    estado_anterior text,
    estado_nuevo text NOT NULL,
    cambiado_por_usuario_id bigint,
    rol text,
    comentario text,
    creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reserva_historial_reserva
    ON public.reserva_historial (reserva_id, creado_en);
