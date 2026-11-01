-- Mejoras del módulo de técnicos. Idempotente: se puede ejecutar más de una vez.
-- Ejecutar en Supabase -> SQL Editor (ya aplicada en el proyecto SantaLuciaBD).

-- 1) Roles: el código ya maneja estos roles, pero el CHECK solo permitía 3.
ALTER TABLE public.usuarios DROP CONSTRAINT IF EXISTS chk_usuarios_rol;
ALTER TABLE public.usuarios
  ADD CONSTRAINT chk_usuarios_rol CHECK (rol::text = ANY (ARRAY[
    'ADMINISTRADOR','SUPERVISOR','TECNICO','RECEPCIONISTA','VENDEDOR','ALMACENERO','CLIENTE'
  ]::text[]));

-- 2) fecha_actualizacion se mantiene sola en reservas y reparaciones.
CREATE OR REPLACE FUNCTION public.fn_set_fecha_actualizacion()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  NEW.fecha_actualizacion := now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_reservas_fecha_actualizacion ON public.reservas;
CREATE TRIGGER trg_reservas_fecha_actualizacion
  BEFORE UPDATE ON public.reservas
  FOR EACH ROW EXECUTE FUNCTION public.fn_set_fecha_actualizacion();

DROP TRIGGER IF EXISTS trg_reparaciones_fecha_actualizacion ON public.reparaciones;
CREATE TRIGGER trg_reparaciones_fecha_actualizacion
  BEFORE UPDATE ON public.reparaciones
  FOR EACH ROW EXECUTE FUNCTION public.fn_set_fecha_actualizacion();

-- 3) Un técnico no puede tener dos citas activas a la misma hora.
CREATE UNIQUE INDEX IF NOT EXISTS uq_reservas_tecnico_horario
  ON public.reservas (tecnico_id, fecha_reserva, hora_inicio)
  WHERE tecnico_id IS NOT NULL AND estado NOT IN ('CANCELADA', 'NO_ASISTIO');

-- 4) Una sola reparación por cita (evita duplicados por doble clic).
CREATE UNIQUE INDEX IF NOT EXISTS uq_reparaciones_reserva
  ON public.reparaciones (reserva_id)
  WHERE reserva_id IS NOT NULL;

-- 5) Agenda: se borran franjas con fechas imposibles (años 0002, 0020, 0202)
--    y se evita que vuelvan a entrar.
DELETE FROM public.agenda_tecnicos WHERE fecha < DATE '2020-01-01';
ALTER TABLE public.agenda_tecnicos DROP CONSTRAINT IF EXISTS ck_agenda_fecha_razonable;
ALTER TABLE public.agenda_tecnicos
  ADD CONSTRAINT ck_agenda_fecha_razonable CHECK (fecha >= DATE '2020-01-01');

-- 6) Agenda de los próximos 14 días (lunes a sábado, 09:00-18:00) para cada técnico.
INSERT INTO public.agenda_tecnicos (tecnico_id, fecha, hora_inicio, hora_fin, disponible)
SELECT t.id, d::date, TIME '09:00', TIME '18:00', true
FROM public.tecnicos t
CROSS JOIN generate_series(current_date + 1, current_date + 14, interval '1 day') AS d
WHERE extract(dow FROM d) <> 0
ON CONFLICT (tecnico_id, fecha, hora_inicio) DO NOTHING;
