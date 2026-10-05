# MVP — Sistema de ventas, inventario, citas y administración (servicio técnico de línea blanca)

Análisis hecho sobre el código de la rama `AlexTaza` (rutas, servicios y plantillas).

## Qué debe tener el MVP

| Módulo | Mínimo imprescindible | Estado en el repo |
|--------|----------------------|-------------------|
| Acceso y roles | Login seguro, roles, gestión de usuarios, bloqueo por intentos | **Hecho** (login + bloqueo por IP + panel de seguridad) |
| Catálogo y ventas web | Catálogo, carrito, checkout, comprobante PDF | **Hecho** |
| Venta en mostrador | Registrar venta presencial (vendedor), pago local, comprobante | **Falta** (el worker no tiene pantalla de venta) |
| Inventario | Stock por producto, **movimientos** (entrada/salida/ajuste), alerta de stock mínimo, compras a proveedor | **Parcial**: se lee `inventario.stock_actual`; no hay pantallas de movimientos, ajustes ni compras |
| Citas | Agendar en línea, disponibilidad, asignar técnico, estados | **Hecho** (agendar, disponibilidad, asignación) · **Falta** cambiar estado (en proceso / completada / cancelada) |
| Órdenes de reparación | Diagnóstico, repuestos usados (descuenta stock), costo, garantía, cierre | **Falta** (tabla en BD, sin pantallas) |
| Clientes | Búsqueda por documento, historial de citas/compras | **Parcial** (autocompletado por documento; sin ficha de cliente) |
| Notificaciones | Aviso de cita confirmada / reparación lista | **Falta** el emisor (KPI-06 solo mide lo que ya existe en la tabla) |
| Administración | Dashboard + KPIs para decidir, exportar CSV | **Hecho** (6 KPIs, filtros, CSV) |

## Orden recomendado para completarlo
1. Cambio de estado de cita y orden de reparación con repuestos (cierra el ciclo de servicio).
2. Movimientos de inventario + stock mínimo (alimenta KPI-04 con datos fiables).
3. Venta en mostrador (reutiliza `carrito_service`, `pago_service` y `pdf_service`).
4. Notificaciones de cita/reparación (alimenta KPI-06).

## Cambios de esta entrega
- **Arranque**: faltaba importar `api` en `create_app` (la app no iniciaba).
- **Login**: 3 fallos → espera de 5 min; 2 fallos más → IP bloqueada hasta que un administrador la desbloquee en `/admin/seguridad`. Ejecutar `docs/sql/001_seguridad_login.sql` (tabla `ip_acceso`). Sin la tabla, funciona en memoria. Recuperación: `flask --app app.py ip:desbloquear <ip>`. Ajustable por `.env`: `LOGIN_MAX_INTENTOS`, `LOGIN_ESPERA_MIN`, `LOGIN_FALLOS_BLOQUEO`, `TRUST_PROXY=1` si hay proxy.
- **KPIs con los datos existentes**: paginación (PostgREST corta en 1000 filas), consultas `.in_()` por lotes, límites de fecha en hora de Perú, y las citas atendidas antes de existir `fecha_primera_atencion` ya no cuentan como incumplimiento en KPI-02.
- **Frontend**: navbar Bootstrap con menú lateral en móvil, `theme.css`, login y registro rediseñados, Bootstrap Icons local.

## Imágenes para iconos
Poner el archivo en `app/static/img/icons/<nombre>.svg|png|webp|jpg` y se usa solo; si no existe, se muestra el icono de Bootstrap. Uso en plantillas: `{{ icono('nav-kpis', 'graph-up-arrow', 20) }}`.
Nombres ya usados: `nav-panel nav-kpis nav-citas nav-productos nav-usuarios nav-seguridad nav-inicio nav-catalogo nav-agendar nav-nosotros nav-carrito login-ventas login-inventario login-citas login-kpis registro error-403 error-404 seg-titulo`.
