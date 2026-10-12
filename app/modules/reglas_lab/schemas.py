from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


LaboratoryType = Literal['QUIMICA', 'FISICA']
Severity = Literal['CRÍTICO', 'ALTO', 'MEDIO']
IncidentStatus = Literal['PENDIENTE', 'EN_REVISION', 'ATENDIDO']


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


class WorkshopCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    laboratory_type: LaboratoryType
    description: str | None = Field(default=None, max_length=500)


class CameraSummary(BaseModel):
    id: int
    name: str
    laboratory_type: LaboratoryType
    status: str


class WorkshopResponse(BaseModel):
    id: int
    name: str
    laboratory_type: LaboratoryType
    description: str | None
    cameras: list[CameraSummary]
    required_equipment_ids: list[str]
    created_at: datetime
    updated_at: datetime


class WorkshopConfigurationUpdate(BaseModel):
    camera_ids: list[int] = Field(default_factory=list)
    required_equipment_ids: list[str] = Field(default_factory=list)


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


class IncidentStatusUpdate(BaseModel):
    status: IncidentStatus


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