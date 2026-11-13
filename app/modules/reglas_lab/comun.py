"""Piezas compartidas por los submódulos de reglas_lab: sesión de base de datos, permisos sobre un
laboratorio, fechas en UTC y tipos de datos comunes."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException

from app.core.database import SessionLocal
from app.core.permisos import can_access_workshop
from app.modules.reglas_lab import models

LaboratoryType = Literal['QUIMICA', 'FISICA']
Severity = Literal['CRÍTICO', 'ALTO', 'MEDIO']


def get_db():
    """Sesión de base de datos por petición (dependencia de FastAPI)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def rol(current_user) -> str:
    return current_user.role.value


def ensure_workshop_access(workshop: models.Workshop, current_user) -> None:
    """Encargado: solo su laboratorio asignado (app/core/permisos.py). El administrador, todos."""
    if not can_access_workshop(current_user, workshop.id):
        raise HTTPException(status_code=403, detail='No tienes acceso a este taller')


def ensure_can_manage(current_user) -> None:
    if rol(current_user) not in {'ADMINISTRADOR', 'ENCARGADO_QUIMICA', 'ENCARGADO_FISICA'}:
        raise HTTPException(status_code=403, detail='No tienes permiso para modificar reglas del laboratorio')


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite devuelve fechas sin zona; se guardan en UTC, así que se marcan como UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def naive_utc(value: datetime) -> datetime:
    """SQLite guarda las fechas en UTC sin zona; los filtros se comparan igual."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
