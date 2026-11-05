from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint

from app.core.database import Base


class Workshop(Base):
    __tablename__ = 'lab_workshops'
    __table_args__ = (UniqueConstraint('name', 'laboratory_type', name='uq_lab_workshop_name_area'),)

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    laboratory_type = Column(String(20), nullable=False)
    description = Column(String(500), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class WorkshopCamera(Base):
    __tablename__ = 'lab_workshop_cameras'
    __table_args__ = (UniqueConstraint('camera_id', name='uq_lab_camera_assignment'),)

    workshop_id = Column(Integer, ForeignKey('lab_workshops.id', ondelete='CASCADE'), primary_key=True)
    camera_id = Column(Integer, ForeignKey('cameras.id', ondelete='CASCADE'), primary_key=True)
    assigned_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class SafetyEquipment(Base):
    __tablename__ = 'lab_safety_equipment'

    id = Column(String(80), primary_key=True)
    name = Column(String(120), nullable=False, unique=True)
    slot = Column(String(30), nullable=False)
    icon = Column(String(20), nullable=False, default='🛡️')
    missing_code = Column(String(40), nullable=False, unique=True)
    missing_description = Column(String(500), nullable=False)
    impact = Column(String(20), nullable=False)
    model_class = Column(String(120), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class WorkshopEquipmentRule(Base):
    __tablename__ = 'lab_workshop_equipment_rules'
    __table_args__ = (UniqueConstraint('workshop_id', 'equipment_id', name='uq_workshop_equipment_rule'),)

    id = Column(Integer, primary_key=True, index=True)
    workshop_id = Column(Integer, ForeignKey('lab_workshops.id', ondelete='CASCADE'), nullable=False)
    equipment_id = Column(String(80), ForeignKey('lab_safety_equipment.id'), nullable=False)
    is_required = Column(Boolean, nullable=False, default=True)
    # Código y gravedad según el reglamento del laboratorio. Si faltan, se usan
    # missing_code e impact del catálogo.
    rule_code = Column(String(40), nullable=True)
    severity = Column(String(20), nullable=True)


class Incident(Base):
    __tablename__ = 'lab_incidents'

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(40), nullable=False, unique=True, index=True)
    workshop_id = Column(Integer, ForeignKey('lab_workshops.id'), nullable=False, index=True)
    camera_id = Column(Integer, ForeignKey('cameras.id'), nullable=False, index=True)
    equipment_id = Column(String(80), ForeignKey('lab_safety_equipment.id'), nullable=True)
    title = Column(String(160), nullable=False)
    description = Column(String(1000), nullable=False)
    severity = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default='PENDIENTE')
    detected_class = Column(String(120), nullable=True)
    confidence = Column(Float, nullable=True)
    bounding_box = Column(JSON, nullable=True)
    frame_width = Column(Integer, nullable=True)
    frame_height = Column(Integer, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=True)  # None = creada por el sistema
    rule_code = Column(String(40), nullable=True)  # regla vigente al detectar
    capture_path = Column(String(300), nullable=True)  # captura con cabezas enmascaradas
    # Validación humana (Tarea 2A): el nombre sale del usuario autenticado.
    reviewed_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_reason = Column(String(300), nullable=True)
    action_taken = Column(String(30), nullable=True)  # CORREGIDO | RETIRADO_DE_PRACTICA
    track_id = Column(Integer, nullable=True)  # persona en la cámara (solo para agrupar faltas)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class IncidentFault(Base):
    """Cada EPP faltante de una alerta, con su propia captura (evidencia).

    Varias faltas de la misma persona en poco tiempo forman una sola alerta.
    """
    __tablename__ = 'lab_incident_faults'

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('lab_incidents.id', ondelete='CASCADE'), nullable=False, index=True)
    equipment_id = Column(String(80), ForeignKey('lab_safety_equipment.id'), nullable=True)
    rule_code = Column(String(40), nullable=True)
    severity = Column(String(20), nullable=False)
    capture_path = Column(String(300), nullable=True)
    present_ratio = Column(Float, nullable=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))