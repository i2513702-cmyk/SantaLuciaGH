# KPIs del negocio (`/admin/kpis`)

Modulo de indicadores del panel, **exclusivo del rol `ADMINISTRADOR`**. Los seis
KPIs se calculan en el servidor leyendo las tablas reales de Supabase con el
cliente REST; el frontend solo dibuja el resultado.

- **Sin migraciones.** No se crean tablas, vistas, funciones ni triggers. Todo
  sale de las tablas que ya existen (`reservas`, `carritos`, `detalle_carrito`,
  `ventas`, `visitas_web`, `productos`, `inventario`, `notificaciones`).
- **Sin datos inventados.** Si un KPI no tiene datos en el periodo, la tarjeta
  lo muestra como "Sin datos" en vez de poner ceros. Si Supabase falla, la
  tarjeta lo marca como error y el resto del panel sigue funcionando.
- **Sin librerias nuevas.** Los graficos son SVG y CSS con los tokens del design
  system que ya usa el panel (`app/static/js/kpis.js`, `app/static/css/style.css`).

## Archivos

| Archivo | Que hace |
|---------|----------|
| `app/services/kpi_service.py` | Calcula los 6 KPIs, el periodo anterior, las series, el CSV y el snapshot semanal |
| `app/routes/kpi.py` | Rutas `/admin/kpis`, `/admin/kpis/datos` (JSON) y `/admin/kpis/csv`, con `@roles_required("admin")` |
| `app/templates/admin/kpis.html` | Franja de cumplimiento, 6 tarjetas y 6 graficos |
| `app/static/js/kpis.js` | Filtros por AJAX y dibujo de graficos |
| `app/services/tracking_service.py` | Registra una visita por sesion en `visitas_web` |
| `app/services/cita_service.py` | `marcar_primera_atencion()` sella la primera atencion de la cita |

## Fórmulas y supuestos

Los tres primeros supuestos son **correcciones al informe** porque el esquema
real no coincide con lo que decía la ficha tecnica.

### KPI-01 · Tiempo medio de primera atención (meta: <= 24 h)
Promedio de `fecha_primera_atencion - fecha_creacion` de las citas no
canceladas que ya tienen `fecha_primera_atencion` en el periodo.
Sin dato: si ninguna cita fue atendida en el periodo.

### KPI-02 · Citas atendidas dentro de 24 h (meta: >= 90 %)
Numerador: citas cuya primera atencion llego dentro de las 24 h.
Denominador: **todas** las citas atendidas en el periodo (lleguen tarde o
temprano) mas las que llevan mas de 24 h sin atencion, que cuentan como
incumplimiento. Una cita atendida a las 273 h **no sale del denominador**: es un
fallo, no un dato descartable.

### KPI-03 · Tasa de conversión web (meta: >= 2 %)
Sesiones que visitaron el sitio en el periodo y compraron en el mismo periodo.
La union se hace por `sesion_id` == `carritos.usuario_sesion` (`user:<id>` o
`anon:<token>`), que es exactamente el mismo identificador que genera
`tracking_service.clave_sesion()`.

> **Suposiciones**
> - `ventas.canal_venta` se guarda como `'TIENDA'`, no `'web'`. Se aceptan
>   `'TIENDA'` y `'WEB'` para que el KPI siga siendo valido si el canal cambia.
> - Las visitas del administrador no se registran: puede abrir el catalogo pero
>   nunca tener carrito, asi que solo bajaria la tasa.
> - Se cuenta **una fila por sesion** en `visitas_web` (la primera visita). Los
>   datos historicos que ya existen tienen varias filas por sesion; el calculo
>   usa `sesiones distintas`, asi que los duplicados no inflan el valor.

### KPI-04 · Disponibilidad de repuestos (meta: >= 85 %)
Productos con `activo = true` cuyo stock (suma de `inventario.stock_actual`) es
mayor que 0. Es una **foto del momento**: el inventario no guarda historico.
Para ver la evolucion, el comando `kpi:snapshot` guarda una foto por semana en
`data/kpi_snapshots.jsonl` y el grafico la usa como serie.

### KPI-05 · Abandono de carrito (meta: <= 70 %)
Carritos con productos que nunca se convirtieron, sobre los carritos con
productos ya resueltos (convertidos + abandonados). Se cuentan como abandonados
los que quedaron `ABANDONADO`/`EXPIRADO` o cuya vigencia vencio, sin venta
asociada.

> **Supuestos**
> - **No existe estado `CONVERTIDO`.** Tras el pago, `carrito_service.finalizar()`
>   deja el carrito en `ABANDONADO` y `checkout` despues llama `vaciar()`, que
>   borra su `detalle_carrito`. Un carrito se considera convertido **si tiene una
>   venta asociada**, no por su estado; si se usara el estado, cada compra
>   contaria como abandono.
> - Por lo mismo, "tuvo productos" se evalua con `detalle_carrito` **o** una venta
>   asociada: los carritos convertidos ya no tienen lineas de detalle.
> - Los carritos todavia `ACTIVO` (en curso) quedan fuera del denominador: si no
>   han tenido tiempo de abandonarse, contarian como fallo.

### KPI-06 · Entrega de notificaciones (meta: >= 95 %)
Notificaciones con `estado_envio` `enviado` o `entregado`, sobre el total de
enviadas, entregadas y fallidas. Se comparan en minusculas porque en la base los
estados vienen asi (`entregado`, `fallido`). La ficha lista los `codigo_error`
mas frecuentes, que es de donde sale la accion correctiva.

> **Supuesto**: el periodo se filtra por `fecha_creacion` de la notificacion
> (no por `fecha_envio`), para que el KPI cuente lo enviado en el periodo.

## Metas configurables

Estan en `app/config.py` y se pueden cambiar por `.env` sin tocar codigo:

```
KPI_META_01_HORAS=24     # menor es mejor
KPI_META_02_PCT=90
KPI_META_03_PCT=2
KPI_META_04_PCT=85
KPI_META_05_PCT=70       # menor es mejor
KPI_META_06_PCT=95
KPI_TOLERANCIA_PCT=10    # margen para el semaforo ambar ("cerca de la meta")
KPI_SNAPSHOT_FILE=data/kpi_snapshots.jsonl
```

## Rutas y seguridad

| Ruta | Para que sirve | Acceso |
|------|----------------|--------|
| `/admin/kpis` | Panel (HTML) | `roles_required("admin")` -> solo `ADMINISTRADOR` |
| `/admin/kpis/datos?desde=&hasta=` | JSON para los filtros | igual |
| `/admin/kpis/csv?desde=&hasta=` | Descarga CSV (con BOM, Excel es-ES) | igual |

El filtro de fechas se valida en el servidor: rango maximo de 3 anos, fechas
invertidas y formatos invalidos devuelven `400` con el motivo, y nunca se
interpola nada en una consulta (todo va por parametros de Supabase).

## Como probarlo

1. Arrancar la app e iniciar sesion con una cuenta `ADMINISTRADOR`.
2. Entrar a `/admin/kpis`. Debe verse la franja "X de 6 KPIs en meta", las 6
   tarjetas con valor, meta, variacion y semaforo, y los 6 graficos.
3. Cambiar el periodo (rango rapido o fechas) y comprobar que la pagina **no**
   recarga y que el enlace "Descargar CSV" sigue al periodo elegido.
4. Probar con otro rol (`/admin/kpis` debe responder **403**).
5. Probar un periodo sin datos (por ejemplo `2099-01-01` a `2099-01-31`): las
   tarjetas deben mostrar "Sin datos" y los graficos su estado vacio, no ceros.
6. CSV: abrir el archivo descargado en Excel; debe tener 6 filas y los acentos
   correctos.

Comandos utiles:

```
flask --app app.py supabase:test     # confirmar conexion con Supabase
flask --app app.py kpi:snapshot      # foto semanal de KPI-04
```

## Dejar la primera atención automática

`reservas.fecha_primera_atencion` ya existe y las 26 citas de la base la tienen
poblada, pero **nada en el código la escribía**. Ahora, cuando el administrador
asigna un técnico a una cita (`POST /admin/citas/<id>/asignar`), se sella con
`cita_service.marcar_primera_atencion()`, que hace un `UPDATE ... WHERE
fecha_primera_atencion IS NULL`: se escribe **una sola vez** y nunca pisa la
marca original, asi que reasignar un técnico no falsea el tiempo de respuesta.

Si en el futuro las citas pasan a un estado "ATENDIDA" desde otra parte del
sistema, hay que llamar a `cita_service.marcar_primera_atencion(reserva_id)`
ahi tambien.
