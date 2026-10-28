-- Crear tabla libro_reclamaciones para Libro de Reclamaciones conforme a Ley 29571 (Perú)
CREATE TABLE IF NOT EXISTS public.libro_reclamaciones (
    id BIGSERIAL PRIMARY KEY,
    numero_registro VARCHAR(50) UNIQUE NOT NULL,
    fecha_registro TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Datos del proveedor (precargados)
    razon_social VARCHAR(255) NOT NULL,
    ruc VARCHAR(11) NOT NULL,
    domicilio_proveedor TEXT NOT NULL,
    
    -- Datos del consumidor
    consumidor_nombres VARCHAR(255) NOT NULL,
    consumidor_apellidos VARCHAR(255) NOT NULL,
    tipo_documento VARCHAR(20) NOT NULL,
    numero_documento VARCHAR(20) NOT NULL,
    domicilio_consumidor TEXT NOT NULL,
    telefono VARCHAR(20) NOT NULL,
    email VARCHAR(255) NOT NULL,
    
    -- Menor de edad
    es_menor BOOLEAN DEFAULT FALSE,
    apoderado_nombre VARCHAR(255),
    apoderado_parentesco VARCHAR(100),
    apoderado_documento VARCHAR(20),
    
    -- Identificación del bien contratado
    tipo_bien VARCHAR(20) NOT NULL, -- PRODUCTO o SERVICIO
    descripcion_bien TEXT NOT NULL,
    monto_reclamado VARCHAR(50),
    
    -- Detalle de la reclamación
    tipo_solicitud VARCHAR(20) NOT NULL, -- RECLAMO o QUEJA
    detalle_hecho TEXT NOT NULL,
    pedido_consumidor TEXT NOT NULL,
    
    -- Respuesta y seguimiento
    estado VARCHAR(20) DEFAULT 'pendiente', -- pendiente, en_proceso, atendido
    respuesta TEXT,
    fecha_respuesta TIMESTAMPTZ,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Índices para búsquedas rápidas
CREATE INDEX IF NOT EXISTS idx_libro_reclamaciones_numero ON public.libro_reclamaciones(numero_registro);
CREATE INDEX IF NOT EXISTS idx_libro_reclamaciones_fecha ON public.libro_reclamaciones(fecha_registro);
CREATE INDEX IF NOT EXISTS idx_libro_reclamaciones_estado ON public.libro_reclamaciones(estado);
CREATE INDEX IF NOT EXISTS idx_libro_reclamaciones_tipo ON public.libro_reclamaciones(tipo_solicitud);
CREATE INDEX IF NOT EXISTS idx_libro_reclamaciones_documento ON public.libro_reclamaciones(numero_documento);

-- Trigger para actualizar updated_at
CREATE OR REPLACE FUNCTION update_libro_reclamaciones_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_libro_reclamaciones_updated_at
    BEFORE UPDATE ON public.libro_reclamaciones
    FOR EACH ROW
    EXECUTE FUNCTION update_libro_reclamaciones_updated_at();

-- Comentarios
COMMENT ON TABLE public.libro_reclamaciones IS 'Libro de Reclamaciones conforme a Ley 29571 - Código de Protección y Defensa del Consumidor Perú';
COMMENT ON COLUMN public.libro_reclamaciones.numero_registro IS 'Número correlativo automático';
COMMENT ON COLUMN public.libro_reclamaciones.tipo_solicitud IS 'RECLAMO o QUEJA';
COMMENT ON COLUMN public.libro_reclamaciones.estado IS 'pendiente, en_proceso, atendido';
