-- Job periódico (cada 5 minutos) que marca como VENCIDA la disponibilidad que ya terminó.
-- Requiere la extensión pg_cron (Supabase: Database -> Extensions). Idempotente.
CREATE EXTENSION IF NOT EXISTS pg_cron;
SELECT cron.unschedule(jobid) FROM cron.job WHERE jobname = 'cerrar-sesiones-vencidas';
SELECT cron.schedule('cerrar-sesiones-vencidas', '*/5 * * * *', $$SELECT public.cerrar_sesiones_vencidas()$$);
