"""Qué puede ver y tocar cada usuario (decisión del 2026-11-11).

- Administrador general: todos los locales, laboratorios, cámaras e incidencias.
- Encargado: solo su laboratorio asignado (users.workshop_id), sus cámaras y sus incidencias.
  Sin laboratorio asignado no ve nada hasta que el administrador le asigne uno.

Todas las rutas usan estas funciones para no repetir la regla en cada lugar.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

SIN_LABORATORIO = 'No tienes un laboratorio asignado. Pide al administrador que te asigne uno.'


def is_admin(user) -> bool:
    return user.role.value == 'ADMINISTRADOR'


def allowed_workshop_ids(user) -> set[int] | None:
    """Laboratorios visibles; None = todos (administrador)."""
    if is_admin(user):
        return None
    return {user.workshop_id} if getattr(user, 'workshop_id', None) else set()


def can_access_workshop(user, workshop_id: int) -> bool:
    allowed = allowed_workshop_ids(user)
    return allowed is None or workshop_id in allowed


def allowed_camera_ids(db: Session, user) -> set[int] | None:
    """Cámaras visibles: las asignadas a su laboratorio; None = todas (administrador)."""
    allowed = allowed_workshop_ids(user)
    if allowed is None:
        return None
    if not allowed:
        return set()
    from app.modules.reglas_lab.models import WorkshopCamera
    rows = db.query(WorkshopCamera.camera_id).filter(WorkshopCamera.workshop_id.in_(allowed)).all()
    return {row.camera_id for row in rows}


def ensure_camera_access(db: Session, user, camera_id: int) -> None:
    allowed = allowed_camera_ids(db, user)
    if allowed is not None and camera_id not in allowed:
        raise HTTPException(status_code=403, detail='No tienes acceso a esta cámara')


def assigned_workshop(db: Session, user):
    """Laboratorio del encargado (para registrar cámaras en él). Error si no tiene uno."""
    from app.modules.reglas_lab.models import Workshop
    workshop = db.get(Workshop, user.workshop_id) if getattr(user, 'workshop_id', None) else None
    if workshop is None or not workshop.is_active:
        raise HTTPException(status_code=403, detail=SIN_LABORATORIO)
    return workshop
