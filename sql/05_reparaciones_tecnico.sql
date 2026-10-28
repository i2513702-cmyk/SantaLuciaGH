-- Añade soporte de técnico y datos de diagnóstico/notas a reparaciones
-- Ejecutar en Supabase SQL Editor. Idempotente.

ALTER TABLE public.reparaciones
ADD COLUMN IF NOT EXISTS tecnico_id integer;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_type = 'FOREIGN KEY'
      AND table_schema = 'public'
      AND table_name = 'reparaciones'
      AND constraint_name = 'reparaciones_tecnico_id_fkey'
  ) THEN
    ALTER TABLE public.reparaciones
    ADD CONSTRAINT reparaciones_tecnico_id_fkey
    FOREIGN KEY (tecnico_id) REFERENCES public.tecnicos(id);
  END IF;
END $$;

ALTER TABLE public.reparaciones
ADD COLUMN IF NOT EXISTS diagnostico text;

ALTER TABLE public.reparaciones
ADD COLUMN IF NOT EXISTS notas_tecnico text;

ALTER TABLE public.reparaciones
ADD COLUMN IF NOT EXISTS costo_estimado numeric;

ALTER TABLE public.reparaciones
ADD COLUMN IF NOT EXISTS fecha_actualizacion timestamptz;
