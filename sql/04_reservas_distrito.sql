-- Distrito de la atención a domicilio en citas (reservas).
-- El formulario de citas divide la dirección en dos casillas: distrito
-- (lista de cobertura) y calle. Al guardar se compone "Distrito - Dirección"
-- en reservas.calle, por lo que esta columna solo se usa si más adelante se
-- quiere consultar/filtrar por distrito sin parsear el texto.
ALTER TABLE public.reservas
ADD COLUMN IF NOT EXISTS distrito VARCHAR(60);
