"""Excepciones personalizadas para la aplicación."""


class AppError(Exception):
    """Base para todas las excepciones de la aplicación."""

    def __init__(self, message="Error de la aplicación", status_code=400, payload=None):
        super().__init__()
        self.message = message
        self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        rv = dict(self.payload or ())
        rv["error"] = self.message
        rv["status_code"] = self.status_code
        return rv


class NotFoundError(AppError):
    """Recurso no encontrado (404)."""

    def __init__(self, message="Recurso no encontrado", payload=None):
        super().__init__(message=message, status_code=404, payload=payload)


class ValidationError(AppError):
    """Error de validación (422)."""

    def __init__(self, message="Error de validación", payload=None):
        super().__init__(message=message, status_code=422, payload=payload)


class ConflictError(AppError):
    """Conflicto con estado actual (409)."""

    def __init__(self, message="Conflicto con el estado actual del recurso", payload=None):
        super().__init__(message=message, status_code=409, payload=payload)


class AuthorizationError(AppError):
    """No autorizado (403)."""

    def __init__(self, message="No tiene permisos para realizar esta acción", payload=None):
        super().__init__(message=message, status_code=403, payload=payload)


class BusinessRuleError(AppError):
    """Regla de negocio violada (422)."""

    def __init__(self, message="Regla de negocio violada", payload=None):
        super().__init__(message=message, status_code=422, payload=payload)
