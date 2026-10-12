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

### Migracion SQL (imagenes de productos)

Para poder guardar la imagen de cada producto hay que crear una vez la columna
`imagen_url` en la tabla `productos` (Supabase -> SQL Editor):

```sql
ALTER TABLE public.productos
ADD COLUMN IF NOT EXISTS imagen_url text;
```

Tambien queda guardada en `sql/01_imagen_url_productos.sql`. Sin esta columna la
web sigue funcionando, solo que los productos no muestran imagen.

## Ejecucion

```
python app.py
```
(tambien: `flask --app app run --debug`)

## Comandos CLI

```
flask --app app.py supabase:test    # verifica conexion con Supabase
flask --app app.py kpi:snapshot     # guarda la foto semanal de KPI-04
flask --app app.py ip:desbloquear <ip>  # desbloquea una IP del login
```

## Rutas principales

| Ruta | Descripcion |
|------|-------------|
| `/` | Inicio publico |
| `/registrarse` | Crear cuenta (empleado + usuario) |
| `/login` , `/logout` | Iniciar / cerrar sesion |
| `/panel` | Redirige segun el rol |
| `/admin` | Panel admin (rol ADMINISTRADOR) |
| `/admin/productos` | Catalogo: crear/editar/eliminar productos y subir su imagen |
| `/admin/configuracion` | Marca de la tienda: cambiar/restaurar el logo (**solo ADMINISTRADOR**) |
| `/admin/kpis` | **KPIs del negocio (solo ADMINISTRADOR)** |
| `/worker` | Panel de trabajo (demas roles) |
| `/sistema/usuarios` | Gestion de usuarios (solo admin) |
| `/admin/seguridad` | IPs con intentos fallidos / bloqueadas; desbloqueo (solo ADMINISTRADOR) |
| `/cambiar_clave` | Cambio de contrasena |
| `/supabase/status` | Estado de la conexion (JSON) |

## Imagenes y logo (Supabase Storage)

Todo se guarda en **Supabase Storage** (buckets publicos) y se enlaza por URL:

| Que | Bucket | Ruta | Quien lo sube |
|-----|--------|------|----------------|
| Imagen de producto | `productos` | `producto-<id>-<aleatorio>.<ext>` | Panel -> Productos -> Nuevo/Editar |
| Logo de Santa Lucia | `logos` | `logo.<ext>` (nombre estable) | Panel -> Configuracion |

- Los buckets `productos` y `logos` se crean solos (publicos) la primera vez que
  se sube una imagen, y tambien al arrancar la app si faltan.
- La URL publica del producto se guarda en `productos.imagen_url` y se muestra en
  la tienda (`/tienda`) y en los destacados del inicio.
- Si hay un logo subido, reemplaza al logo estatico en la barra superior, el pie
  de pagina y el login; "Restaurar logo predeterminado" lo elimina del bucket.
- Formatos permitidos: PNG, JPG, JPEG, WEBP y SVG. Tamano maximo: 2 MB.
- Utilidades: `app/services/storage_service.py`.

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