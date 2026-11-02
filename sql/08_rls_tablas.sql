-- 08_rls_tablas.sql
-- Idempotente. NO ejecutar sin revisar el inventario previo.
-- Activa RLS en TODAS las tablas de public. La app accede con la service key
-- (get_admin_client), que omite RLS, por lo que no debe romperse.
-- Si alguna parte del codigo usa la anon key (SUPABASE_KEY), dejaria de funcionar.

DO $$
DECLARE t text;
BEGIN
    FOR t IN (
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
    ) LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;

-- Verificacion (ejecutar manualmente):
-- SELECT schemaname, tablename, rowsecurity
-- FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
