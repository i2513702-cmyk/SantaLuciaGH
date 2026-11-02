-- 12_ventas_efectivo.sql
-- Idempotente.
ALTER TABLE public.ventas
    ADD COLUMN IF NOT EXISTS fecha_limite_pago timestamptz;
