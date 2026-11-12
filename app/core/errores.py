"""Mensajes de error en español para todo lo que llega a la pantalla.

FastAPI y Starlette responden en inglés por defecto ("Not authenticated", "Not Found",
"Field required"...). La web muestra el campo `detail` tal cual, así que aquí se traduce
y, en los errores de validación, se convierte la lista de errores en un texto legible.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Mensajes por defecto de FastAPI/Starlette.
MENSAJES = {
    'Not authenticated': 'Inicia sesión para continuar',
    'Not Found': 'No encontrado',
    'Method Not Allowed': 'Método no permitido',
    'Unauthorized': 'No autorizado',
    'Forbidden': 'Acceso denegado',
    'Internal Server Error': 'Error interno del servidor',
    'Bad Request': 'Solicitud no válida',
    'Unprocessable Entity': 'Datos no válidos',
}

# Tipos de error de validación (pydantic) más comunes.
VALIDACION = {
    'missing': 'es obligatorio',
    'string_too_short': 'es demasiado corto',
    'string_too_long': 'es demasiado largo',
    'string_pattern_mismatch': 'tiene un formato no válido',
    'string_type': 'debe ser texto',
    'int_parsing': 'debe ser un número entero',
    'int_type': 'debe ser un número entero',
    'float_parsing': 'debe ser un número',
    'float_type': 'debe ser un número',
    'bool_parsing': 'debe ser verdadero o falso',
    'greater_than': 'es demasiado pequeño',
    'greater_than_equal': 'es demasiado pequeño',
    'less_than': 'es demasiado grande',
    'less_than_equal': 'es demasiado grande',
    'literal_error': 'no es una opción válida',
    'enum': 'no es una opción válida',
    'json_invalid': 'no es un JSON válido',
    'value_error': 'no es válido',
}

# Ubicaciones del dato que no aportan al mensaje.
_UBICACIONES = {'body', 'query', 'path', 'header', 'cookie'}


def traducir(detalle):
    return MENSAJES.get(detalle, detalle) if isinstance(detalle, str) else detalle


def texto_validacion(errores: list[dict]) -> str:
    partes = []
    for error in errores:
        campo = '.'.join(str(parte) for parte in error.get('loc', ()) if parte not in _UBICACIONES)
        motivo = VALIDACION.get(error.get('type', ''), 'no es válido')
        partes.append(f'el campo «{campo}» {motivo}' if campo else f'el dato enviado {motivo}')
    return 'Datos no válidos: ' + '; '.join(partes) if partes else MENSAJES['Unprocessable Entity']


def registrar(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def error_http(_request: Request, exc: StarletteHTTPException):
        return JSONResponse({'detail': traducir(exc.detail)}, status_code=exc.status_code,
                            headers=getattr(exc, 'headers', None))

    @app.exception_handler(RequestValidationError)
    async def error_validacion(_request: Request, exc: RequestValidationError):
        return JSONResponse({'detail': texto_validacion(exc.errors())}, status_code=422)
