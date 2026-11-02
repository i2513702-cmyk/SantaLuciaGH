-- 11_ajustar_stock.sql
-- Ejecutar DESPUES de revisar duplicados en inventario.
--
-- 1) Consolidar filas duplicadas (ejecutar solo si el Paso 0 mostro duplicados):
-- UPDATE public.inventario i
-- SET stock_actual = s.total
-- FROM (SELECT producto_id, SUM(stock_actual) AS total FROM public.inventario GROUP BY producto_id) s
-- WHERE i.producto_id = s.producto_id
--   AND i.id = (SELECT MIN(id) FROM public.inventario WHERE producto_id = i.producto_id);
-- DELETE FROM public.inventario i
-- WHERE i.id NOT IN (SELECT MIN(id) FROM public.inventario GROUP BY producto_id);
--
-- 2) Indice unico para upsert:
CREATE UNIQUE INDEX IF NOT EXISTS uq_inventario_producto
    ON public.inventario (producto_id);

-- 3) Funcion atomica de ajuste de stock:
CREATE OR REPLACE FUNCTION public.ajustar_stock(p_producto_id bigint, p_delta numeric)
RETURNS numeric
LANGUAGE plpgsql
AS $$
DECLARE
    v_stock numeric;
BEGIN
    INSERT INTO public.inventario (producto_id, stock_actual, stock_reservado)
    VALUES (p_producto_id, 0, 0)
    ON CONFLICT (producto_id) DO NOTHING;

    UPDATE public.inventario
    SET stock_actual = stock_actual + p_delta,
        fecha_actualizacion = now()
    WHERE producto_id = p_producto_id
      AND stock_actual + p_delta >= 0;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Stock insuficiente para el ajuste solicitado';
    END IF;

    SELECT COALESCE(SUM(stock_actual), 0) INTO v_stock
    FROM public.inventario
    WHERE producto_id = p_producto_id;

    RETURN v_stock;
END;
$$;
