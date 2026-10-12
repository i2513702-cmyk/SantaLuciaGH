-- Control de intentos de login por IP. Ejecutar una vez en Supabase (SQL Editor).
create table if not exists public.ip_acceso (
    ip                text primary key,
    intentos_fallidos integer     not null default 0,
    bloqueada         boolean     not null default false,
    bloqueo_hasta     timestamptz,
    ultimo_usuario    text,
    ultimo_intento    timestamptz,
    fecha_bloqueo     timestamptz,
    desbloqueada_por  text,
    fecha_desbloqueo  timestamptz
);

create index if not exists ix_ip_acceso_bloqueada on public.ip_acceso (bloqueada);

-- El backend usa la service key (omite RLS); se activa RLS para que la anon key no lea esta tabla.
alter table public.ip_acceso enable row level security;
