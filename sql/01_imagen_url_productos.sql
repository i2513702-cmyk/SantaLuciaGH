-- MIGRACIÓN: imágenes de productos (Santa Lucía)
-- Ejecutar en Supabase -> SQL Editor (una sola vez).
--
-- Agrega la columna `imagen_url` a la tabla `productos`; ahí se guarda la
-- URL pública de la imagen subida al bucket `productos` de Storage.

ALTER TABLE public.productos ADD COLUMN IF NOT EXISTS imagen_url text;