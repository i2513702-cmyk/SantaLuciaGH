-- Vigencia de la disponibilidad (agenda_tecnicos) y bloqueo de reservas en el pasado.
-- Idempotente. Zona horaria de negocio: America/Lima (la base corre en UTC, por eso
-- NO se usa current_date: entre las 19:00 y las 24:00 de Lima la base ya "está en mañana").

-- 1) Hora actual de Lima
CREATE OR REPLACE FUNCTION public.ahora_lima()
RETURNS timestamp
LANGUAGE sql
STABLE
SET search_path = public
AS $$ SELECT (now() AT TIME ZONE 'America/Lima') $$;

-- 2) Ciclo de vida de la sesión: ACTIVA -> VENCIDA (nunca se borra, queda como historial).
--    `disponible` sigue siendo el interruptor manual del técnico (Activa/Inactiva).
ALTER TABLE public.agenda_tecnicos
  ADD COLUMN IF NOT EXISTS estado varchar(10) NOT NULL DEFAULT 'ACTIVA';
ALTER TABLE public.agenda_tecnicos DROP CONSTRAINT IF EXISTS ck_agenda_estado;
ALTER TABLE public.agenda_tecnicos
  ADD CONSTRAINT ck_agenda_estado CHECK (estado IN ('ACTIVA', 'VENCIDA'));
CREATE INDEX IF NOT EXISTS idx_agenda_activas_fecha
  ON public.agenda_tecnicos (fecha) WHERE estado = 'ACTIVA';

-- 3) Validación en la base: no crear ni editar sesiones en el pasado.
CREATE OR REPLACE FUNCTION public.fn_validar_franja_agenda()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  ahora timestamp := date_trunc('minute', public.ahora_lima());
  cambia_horario boolean;
BEGIN
  cambia_horario := TG_OP = 'INSERT'
    OR NEW.fecha       IS DISTINCT FROM OLD.fecha
    OR NEW.hora_inicio IS DISTINCT FROM OLD.hora_inicio
    OR NEW.hora_fin    IS DISTINCT FROM OLD.hora_fin;

  IF cambia_horario THEN
    IF NEW.fecha < ahora::date THEN
      RAISE EXCEPTION 'No se puede registrar disponibilidad con una fecha anterior a hoy (%).',
        to_char(NEW.fecha, 'DD/MM/YYYY') USING ERRCODE = '23514';
    END IF;
    IF NEW.fecha = ahora::date AND NEW.hora_inicio < ahora::time THEN
      RAISE EXCEPTION 'La hora de inicio (%) ya pasó; en Lima son las %.',
        left(NEW.hora_inicio::text, 5), to_char(ahora, 'HH24:MI') USING ERRCODE = '23514';
    END IF;
    NEW.estado := 'ACTIVA';
  ELSIF OLD.estado = 'VENCIDA' AND NEW.estado = 'ACTIVA' THEN
    RAISE EXCEPTION 'Una sesión vencida no se puede reactivar; crea una nueva.' USING ERRCODE = '23514';
  ELSIF OLD.estado = 'VENCIDA' AND NEW.disponible IS DISTINCT FROM OLD.disponible THEN
    RAISE EXCEPTION 'La sesión está vencida y ya no se puede modificar.' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_agenda_validar_franja ON public.agenda_tecnicos;
CREATE TRIGGER trg_agenda_validar_franja
  BEFORE INSERT OR UPDATE ON public.agenda_tecnicos
  FOR EACH ROW EXECUTE FUNCTION public.fn_validar_franja_agenda();

-- 4) Cierre de sesiones vencidas (se conservan, solo cambian a VENCIDA).
CREATE OR REPLACE FUNCTION public.cerrar_sesiones_vencidas(p_ahora timestamp DEFAULT public.ahora_lima())
RETURNS integer
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE n integer;
BEGIN
  UPDATE public.agenda_tecnicos
     SET estado = 'VENCIDA'
   WHERE estado = 'ACTIVA' AND (fecha + hora_fin) <= p_ahora;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END;
$$;
REVOKE ALL ON FUNCTION public.cerrar_sesiones_vencidas(timestamp) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.cerrar_sesiones_vencidas(timestamp) TO service_role;

-- 5) Corrección de los datos existentes: lo pasado que figuraba como activo pasa a VENCIDA.
SELECT public.cerrar_sesiones_vencidas();

-- 6) Reservas: una cita abierta no puede quedar en una fecha/hora que ya pasó (hora de Lima).
--    Solo se valida al crear o al cambiar fecha/hora. Para cargar datos históricos a propósito:
--    SET LOCAL app.permitir_pasado = 'on';
CREATE OR REPLACE FUNCTION public.fn_validar_reserva_vigente()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  ahora timestamp := date_trunc('minute', public.ahora_lima());
BEGIN
  IF coalesce(current_setting('app.permitir_pasado', true), '') = 'on' THEN
    RETURN NEW;
  END IF;
  IF NEW.estado IN ('PENDIENTE', 'CONFIRMADA', 'ASIGNADA')
     AND (TG_OP = 'INSERT'
          OR NEW.fecha_reserva IS DISTINCT FROM OLD.fecha_reserva
          OR NEW.hora_inicio   IS DISTINCT FROM OLD.hora_inicio) THEN
    IF NEW.fecha_reserva < ahora::date THEN
      RAISE EXCEPTION 'No se puede reservar una cita en una fecha anterior a hoy (%).',
        to_char(NEW.fecha_reserva, 'DD/MM/YYYY') USING ERRCODE = '23514';
    END IF;
    IF NEW.fecha_reserva = ahora::date AND NEW.hora_inicio < ahora::time THEN
      RAISE EXCEPTION 'La hora de la cita (%) ya pasó; en Lima son las %.',
        left(NEW.hora_inicio::text, 5), to_char(ahora, 'HH24:MI') USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_reservas_validar_vigente ON public.reservas;
CREATE TRIGGER trg_reservas_validar_vigente
  BEFORE INSERT OR UPDATE ON public.reservas
  FOR EACH ROW EXECUTE FUNCTION public.fn_validar_reserva_vigente();
