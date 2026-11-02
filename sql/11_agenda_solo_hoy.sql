-- La disponibilidad del técnico solo se puede registrar (o mover) para el día de hoy (America/Lima).
-- Reemplaza la función de validación de 09_agenda_vigencia.sql añadiendo el límite superior.
-- Idempotente. Las sesiones futuras que ya existan se conservan: el trigger solo valida
-- cuando se crea una sesión o se cambia su fecha/hora (activar, desactivar y cerrar no se ven afectados).
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
    IF NEW.fecha > ahora::date THEN
      RAISE EXCEPTION 'Solo se puede registrar disponibilidad para el día de hoy (%); no para %.',
        to_char(ahora, 'DD/MM/YYYY'), to_char(NEW.fecha, 'DD/MM/YYYY') USING ERRCODE = '23514';
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
