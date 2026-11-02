-- Fija el search_path de las funciones de trigger (aviso "function_search_path_mutable"
-- del linter de Supabase). Idempotente. Ya aplicada en el proyecto SantaLuciaBD.
ALTER FUNCTION public.fn_actualizar_totales_venta() SET search_path = public;
ALTER FUNCTION public.update_libro_reclamaciones_updated_at() SET search_path = public;
