"""Datos que reciben y devuelven las rutas de incidencias (alertas)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.reglas_lab.comun import Severity

# PENDIENTE = alerta sin revisar. CONFIRMADA / DESCARTADA las fija el encargado o el
# administrador (Tarea 2A). EN_REVISION y ATENDIDO se conservan solo para leer datos anteriores:
# ya no hay forma de asignarlos.
IncidentStatus = Literal['PENDIENTE', 'EN_REVISION', 'CONFIRMADA', 'DESCARTADA', 'ATENDIDO']
ReviewDecision = Literal['CONFIRMADA', 'DESCARTADA']
ReviewAction = Literal['CORREGIDO', 'RETIRADO_DE_PRACTICA']


class IncidentCreate(BaseModel):
    workshop_id: int
    camera_id: int
    equipment_id: str | None = Field(default=None, max_length=80)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1000)
    severity: Severity
    detected_class: str | None = Field(default=None, max_length=120)
    confidence: float | None = Field(default=None, ge=0, le=1)
    bounding_box: list[float] | None = Field(default=None, min_length=4, max_length=4)
    frame_width: int | None = Field(default=None, gt=0)
    frame_height: int | None = Field(default=None, gt=0)
    occurred_at: datetime | None = None


class IncidentReview(BaseModel):
    decision: ReviewDecision
    action: ReviewAction | None = None  # obligatoria al confirmar
    reason: str | None = Field(default=None, max_length=300)  # opcional al descartar


class IncidentFaultResponse(BaseModel):
    id: int | None  # None en incidencias anteriores a la agrupación
    equipment_id: str | None
    equipment_name: str | None
    rule_code: str | None
    severity: Severity
    has_capture: bool
    detected_at: datetime
    detail: str | None = None  # BATA_ABIERTA: el EPP está, pero mal puesto


class IncidentResponse(BaseModel):
    id: int
    code: str
    workshop_id: int
    workshop_name: str
    camera_id: int
    camera_name: str
    equipment_id: str | None
    equipment_name: str | None
    title: str
    description: str
    severity: Severity
    status: IncidentStatus
    detected_class: str | None
    confidence: float | None
    bounding_box: list[float] | None
    frame_width: int | None
    frame_height: int | None
    occurred_at: datetime
    rule_code: str | None = None
    has_capture: bool = False
    created_by_system: bool = False
    reviewed_by_name: str | None = None
    reviewed_at: datetime | None = None
    review_reason: str | None = None
    action_taken: ReviewAction | None = None
    faults: list[IncidentFaultResponse] = Field(default_factory=list)
    pending_seconds: int | None = None  # solo para alertas pendientes
    escalated: bool = False  # pendiente más tiempo del permitido: se avisa al administrador
    session_id: int | None = None  # práctica en la que ocurrió (None: anterior a las sesiones)
    session_code: str | None = None
