-- Cierre de caja: registro diario del arqueo de la caja del negocio.
-- Un cierre por fecha (índice único) con lo esperado por pagos confirmados
-- y lo contado físicamente, guardando la diferencia resultante.

CREATE TABLE IF NOT EXISTS public.cierres_caja (
    id             BIGSERIAL PRIMARY KEY,
    fecha          DATE NOT NULL,
    monto_apertura NUMERIC(12, 2) NOT NULL DEFAULT 0,
    total_efectivo NUMERIC(12, 2) NOT NULL DEFAULT 0,
    total_otros    NUMERIC(12, 2) NOT NULL DEFAULT 0,
    total_ventas   NUMERIC(12, 2) NOT NULL DEFAULT 0,
    monto_contado  NUMERIC(12, 2) NOT NULL DEFAULT 0,
    diferencia     NUMERIC(12, 2) NOT NULL DEFAULT 0,
    n_pagos        INTEGER NOT NULL DEFAULT 0,
    responsable    VARCHAR(120),
    usuario_id     BIGINT,
    observaciones  TEXT,
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS cierres_caja_fecha_uk
    ON public.cierres_caja (fecha);
