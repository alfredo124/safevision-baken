"""Compatibilidad: la bitácora se registra con app.core.bitacora.registrar."""
from app.core.bitacora import registrar


def log_event(db, user, action: str, module: str, description: str = '') -> None:
    """Nombre anterior de app.core.bitacora.registrar."""
    registrar(db, user, action, module, description)
