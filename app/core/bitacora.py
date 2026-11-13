"""Bitácora de actividad (tabla activity_logs): quién hizo qué, cuándo y desde qué equipo.

Se registra después de guardar el cambio, con un código corto (accion), el área (modulo) y un texto
legible (descripcion). Las contraseñas nunca se escriben en la bitácora.

La IP del equipo la pone el middleware de app/main.py en `ip_actual` al empezar cada petición, así
las rutas no necesitan recibir el Request solo para esto.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import datetime, timezone

from sqlalchemy.orm import Session

ip_actual: ContextVar[str | None] = ContextVar('ip_actual', default=None)

# Áreas que aparecen en la bitácora (la pantalla las usa como filtro).
MODULOS = ('usuarios', 'locales', 'laboratorios', 'camaras', 'practicas', 'incidencias', 'reportes')


def registrar(db: Session, usuario, accion: str, modulo: str, descripcion: str = '',
              *, usuario_nombre: str | None = None) -> None:
    """Añade una fila a la bitácora y la guarda.

    `usuario` es el usuario autenticado (o None, p. ej. en un inicio de sesión fallido; entonces
    `usuario_nombre` indica qué nombre se intentó). Si la bitácora falla, la acción ya está guardada:
    se deja constancia en el log del servidor y no se interrumpe la respuesta.
    """
    from app.modules.auth_db.models import ActivityLog  # el modelo vive en el módulo de usuarios

    nombre = usuario_nombre or (getattr(usuario, 'full_name', None) or getattr(usuario, 'username', None))
    try:
        db.add(ActivityLog(
            user_id=getattr(usuario, 'id', None),
            user_name=(nombre or '')[:120] or None,
            action=accion,
            module_affected=modulo,
            description=(descripcion or '')[:500] or None,
            timestamp=datetime.now(timezone.utc),
            ip_address=ip_actual.get(),
        ))
        db.commit()
    except Exception:  # noqa: BLE001  la bitácora nunca debe tumbar la acción ya hecha
        db.rollback()
        logging.getLogger(__name__).exception('No se pudo registrar en la bitácora: %s %s', modulo, accion)
