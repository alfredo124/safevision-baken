"""Configuración por variables de entorno (sin secretos escritos en el código)."""
import os

# Orígenes de la red local: el propio equipo y las IP privadas (LAN), con cualquier puerto.
LAN_ORIGIN_REGEX = (
    r'^https?://('
    r'localhost|127\.0\.0\.1|\[::1\]'
    r'|10\.\d{1,3}\.\d{1,3}\.\d{1,3}'
    r'|192\.168\.\d{1,3}\.\d{1,3}'
    r'|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}'
    r')(:\d+)?$'
)


def cors_origins() -> list[str]:
    """Orígenes extra separados por comas, por ejemplo un dominio de Vercel o de un túnel:
    SAFEVISION_CORS_ORIGINS=https://safevision.vercel.app,https://mi-tunel.example"""
    raw = os.environ.get('SAFEVISION_CORS_ORIGINS', '')
    return [origin.strip().rstrip('/') for origin in raw.split(',') if origin.strip()]


def allow_lan_origins() -> bool:
    """SAFEVISION_CORS_LAN=0 desactiva los orígenes de la red local (solo los de la lista)."""
    return os.environ.get('SAFEVISION_CORS_LAN', '1').strip().lower() not in ('0', 'false', 'no')
