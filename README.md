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
flask --app app.py supabase:test    # verifica conexion con Supabase
flask --app app.py kpi:snapshot     # guarda la foto semanal de KPI-04
```

## Rutas principales

| Ruta | Descripcion |
|------|-------------|
| `/` | Inicio publico |
| `/registrarse` | Crear cuenta (empleado + usuario) |
| `/login` , `/logout` | Iniciar / cerrar sesion |
| `/panel` | Redirige segun el rol |
| `/admin` | Panel admin (rol ADMINISTRADOR) |
| `/admin/kpis` | **KPIs del negocio (solo ADMINISTRADOR)** |
| `/worker` | Panel de trabajo (demas roles) |
| `/sistema/usuarios` | Gestion de usuarios (solo admin) |
| `/cambiar_clave` | Cambio de contrasena |
| `/supabase/status` | Estado de la conexion (JSON) |

## KPIs (indicadores del negocio)

Modulo del panel administrativo exclusivo del rol `ADMINISTRADOR` (`/admin/kpis`).
Los seis indicadores se calculan en el servidor (`app/services/kpi_service.py`)
leyendo las tablas reales de Supabase: **no hay migraciones, vistas ni funciones
nuevas en la base de datos**, y el frontend solo dibuja.

| KPI | Indicador | Meta | De donde sale el dato |
|-----|-----------|------|----------------------|
| KPI-01 | Tiempo medio de primera atencion | 24 h o menos | `reservas.fecha_primera_atencion` - `fecha_creacion` |
| KPI-02 | Citas atendidas dentro de 24 h | 90 % | mismas reservas, contando como fallo las que pasan de 24 h sin atencion |
| KPI-03 | Tasa de conversion web | 2 % | `visitas_web.sesion_id` unido a `ventas.carrito_id` -> `carritos.usuario_sesion` |
| KPI-04 | Disponibilidad de repuestos | 85 % | `productos.activo` con `inventario.stock_actual` > 0 (foto semanal) |
| KPI-05 | Abandono de carrito | 70 % o menos | `carritos` + `detalle_carrito` + `ventas` |
| KPI-06 | Entrega de notificaciones | 95 % | `notificaciones.estado_envio` y `codigo_error` |

Ver `docs/kpis.md` para el detalle de cada formula, los supuestos aplicados al
esquema real, las metas configurables y como probarlo.