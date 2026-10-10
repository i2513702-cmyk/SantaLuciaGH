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

### Consulta de documentos (DNI / RUC / CE)

El autollenado de formularios consulta APIs externas de identidad. Los tokens
son secretos: van en tu `.env` local y **no se suben al repositorio**.

| Variable | Descripcion | Default |
|----------|-------------|---------|
| `APIPERU_TOKEN` | Token de apiperu.dev (DNI y RUC) | vacio |
| `APIPERU_DNI_URL` | Endpoint DNI de apiperu.dev | `https://apiperu.dev/api/dni` |
| `APIPERU_RUC_URL` | Endpoint RUC de apiperu.dev | `https://apiperu.dev/api/ruc` |
| `JSONPE_TOKEN` | Token de json.pe (DNI, RUC y CE) | vacio |
| `JSONPE_DNI_URL` | Endpoint DNI de json.pe | `https://api.json.pe/api/dni` |
| `JSONPE_RUC_URL` | Endpoint RUC de json.pe | `https://api.json.pe/api/ruc` |
| `JSONPE_CE_URL` | Endpoint CE de json.pe | `https://api.json.pe/api/ce` |
| `IDENTIDAD_TIMEOUT` | Timeout (s) por consulta | `6` |

Si el primer proveedor de un documento falla, se intenta el siguiente (fallback):
DNI y RUC usan apiperu.dev primero y json.pe de respaldo; el CE solo existe en
json.pe. El **pasaporte** no tiene API de consulta comercial (solo la web de
Migraciones con captcha): se registra manualmente sin autollenado.

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
| `POST /identidad/consultar` | Autollenado DNI/RUC/CE (publico, JSON, requiere token CSRF) |
| `POST /identidad/consultar-empleado` | Autollenado + vinculo de empleado por documento (solo admin) |