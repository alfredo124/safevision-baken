"""Datos que reciben y devuelven las rutas de laboratorios, locales y catálogo de EPP."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.reglas_lab.comun import LaboratoryType, Severity


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
