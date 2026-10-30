-- Agrega el tipo de electrodoméstico MICROONDAS al catálogo de citas.
-- Ejecutar en Supabase SQL Editor. Idempotente: no duplica si ya existe.

INSERT INTO public.tipos_electrodomestico (nombre, descripcion)
SELECT 'MICROONDAS', 'Hornos microondas'
WHERE NOT EXISTS (
    SELECT 1
    FROM public.tipos_electrodomestico
    WHERE upper(nombre) = 'MICROONDAS'
);
