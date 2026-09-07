# SantaLucia Backend

Sistema de gestión de tienda + reparación de electrodomésticos. Portal web en Flask que opera directo contra la **API REST de Supabase (PostgREST)**: login real, registro y paneles por rol, usando los datos de la base de datos (sin datos de prueba).

## Requisitos

- Python 3.12+
- Proyecto Supabase con las tablas creadas y las claves en `.env`

## Instalacion

```
pip install -r requirements.txt
```

## Configuracion

Crear el archivo `.env` a partir de `.env.example` con:
- `SUPABASE_URL` — URL del proyecto (`https://<ref>.supabase.co`)
- `SUPABASE_KEY` — API key publishable (anon)
- `SUPABASE_SERVICE_KEY` — service_role (solo servidor; omite RLS para lecturas y escrituras)
- `SUPABASE_WEBHOOK_SECRET` — secreto de webhooks (opcional)
- `SECRET_KEY` — clave de sesiones Flask

## Ejecucion

```
python app.py
```
(tambien: `flask --app app run --debug`)

## Comandos CLI

```
flask --app app supabase:test    # verifica conexion con Supabase
```

## Rutas principales

| Ruta | Descripcion |
|------|-------------|
| `/` | Inicio publico |
| `/registrarse` | Crear cuenta (empleado + usuario) |
| `/login` , `/logout` | Iniciar / cerrar sesion |
| `/panel` | Redirige segun el rol |
| `/admin` | Panel admin (rol ADMINISTRADOR) |
| `/worker` | Panel de trabajo (demas roles) |
| `/sistema/usuarios` | Gestion de usuarios (solo admin) |
| `/cambiar_clave` | Cambio de contrasena |
| `/supabase/status` | Estado de la conexion (JSON) |