from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


LaboratoryType = Literal['QUIMICA', 'FISICA']
Severity = Literal['CRÍTICO', 'ALTO', 'MEDIO']
# PENDIENTE = alerta sin revisar. CONFIRMADA / DESCARTADA las fija el encargado o el
# administrador (Tarea 2A). EN_REVISION y ATENDIDO se conservan solo para leer datos anteriores:
# ya no hay forma de asignarlos.
IncidentStatus = Literal['PENDIENTE', 'EN_REVISION', 'CONFIRMADA', 'DESCARTADA', 'ATENDIDO']
ReviewDecision = Literal['CONFIRMADA', 'DESCARTADA']
ReviewAction = Literal['CORREGIDO', 'RETIRADO_DE_PRACTICA']


class SafetyEquipmentCreate(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(min_length=1, max_length=120)
    slot: str = Field(min_length=1, max_length=30)
    icon: str = Field(default='🛡️', max_length=20)
    missing_code: str = Field(min_length=1, max_length=40)
    missing_description: str = Field(min_length=1, max_length=500)
    impact: Severity
    model_class: str | None = Field(default=None, max_length=120)


class SafetyEquipmentResponse(SafetyEquipmentCreate):
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class CampusCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    city: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, max_length=200)


class CampusUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    city: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None


class CampusResponse(BaseModel):
    id: int
    name: str
    city: str | None
    address: str | None
    is_active: bool
    workshop_count: int = 0

    class Config:
        from_attributes = True


class WorkshopCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    laboratory_type: LaboratoryType
    description: str | None = Field(default=None, max_length=500)
    campus_id: int | None = None


class WorkshopUpdate(BaseModel):
    """Datos generales del laboratorio (solo el administrador general)."""
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    campus_id: int | None = None


class CameraSummary(BaseModel):
    id: int
    name: str
    laboratory_type: LaboratoryType
    status: str


class WorkshopRule(BaseModel):
    equipment_id: str = Field(min_length=1, max_length=80)
    rule_code: str | None = Field(default=None, max_length=40)
    severity: Severity | None = None


class WorkshopRuleResponse(BaseModel):
    equipment_id: str
    rule_code: str
    severity: Severity
    manual_verification: bool


class WorkshopResponse(BaseModel):
    id: int
    name: str
    laboratory_type: LaboratoryType
    description: str | None
    campus_id: int | None = None
    campus_name: str | None = None
    cameras: list[CameraSummary]
    required_equipment_ids: list[str]
    rules: list[WorkshopRuleResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class WorkshopConfigurationUpdate(BaseModel):
    camera_ids: list[int] = Field(default_factory=list)
    required_equipment_ids: list[str] = Field(default_factory=list)
    # Opcional: código y gravedad por EPP. Si se envía, reemplaza a required_equipment_ids.
    rules: list[WorkshopRule] | None = None


class RulesConfigurationResponse(BaseModel):
    workshops: list[WorkshopResponse]
    catalog: list[SafetyEquipmentResponse]
    available_cameras: list[CameraSummary]


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


class ReportEmit(BaseModel):
    """Emitir un reporte: mismo periodo y filtros que la vista previa, más el responsable SST."""
    start: datetime
    end: datetime
    workshop_id: int | None = None
    campus_id: int | None = None
    responsible: str = Field(default='', max_length=160)


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
