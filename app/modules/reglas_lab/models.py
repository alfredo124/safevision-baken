from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint

from app.core.database import Base


class Campus(Base):
    """Local (sede) de la universidad. Lo registra el administrador general; cada laboratorio pertenece a uno."""
    __tablename__ = 'lab_campuses'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False, unique=True)
    city = Column(String(80), nullable=True)
    address = Column(String(200), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class Workshop(Base):
    __tablename__ = 'lab_workshops'
    __table_args__ = (UniqueConstraint('name', 'laboratory_type', name='uq_lab_workshop_name_area'),)

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    laboratory_type = Column(String(20), nullable=False)
    description = Column(String(500), nullable=True)
    # Local donde está el laboratorio. None en laboratorios creados antes de registrar locales.
    campus_id = Column(Integer, ForeignKey('lab_campuses.id'), nullable=True, index=True)
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


class PracticeSession(Base):
    """Una práctica en un laboratorio: desde "Iniciar práctica" hasta "Finalizar práctica".

    Es el registro de lo ocurrido en la sala: las incidencias detectadas durante la práctica guardan
    su session_id. El nombre del laboratorio y del local se copian al iniciar, igual que en las
    incidencias, para que el historial no cambie si después se renombran.
    """
    __tablename__ = 'lab_practice_sessions'

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(40), nullable=False, unique=True, index=True)
    workshop_id = Column(Integer, ForeignKey('lab_workshops.id'), nullable=False, index=True)
    workshop_name = Column(String(120), nullable=True)
    campus_name = Column(String(120), nullable=True)
    laboratory_type = Column(String(20), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    started_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)  # None = en curso
    ended_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    # FINALIZADA: la cerró un usuario. INTERRUMPIDA: el servidor se apagó con la práctica en curso
    # (ended_at es entonces la última actividad conocida, no la hora exacta).
    end_reason = Column(String(20), nullable=True)


class Incident(Base):
    __tablename__ = 'lab_incidents'

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(40), nullable=False, unique=True, index=True)
    workshop_id = Column(Integer, ForeignKey('lab_workshops.id'), nullable=False, index=True)
    camera_id = Column(Integer, ForeignKey('cameras.id'), nullable=False, index=True)
    # Datos del momento de la incidencia: el historial no cambia si la cámara se elimina o se renombra,
    # ni si el taller se desactiva. La incidencia es de lo ocurrido, no de la cámara.
    camera_name = Column(String(120), nullable=True)
    workshop_name = Column(String(120), nullable=True)
    laboratory_type = Column(String(20), nullable=True)
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
    # Práctica en la que ocurrió. None en incidencias anteriores a las sesiones o fuera de una práctica.
    session_id = Column(Integer, ForeignKey('lab_practice_sessions.id'), nullable=True, index=True)
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
    # None = el EPP falta; BATA_ABIERTA = está, pero mal puesto (falta leve).
    detail = Column(String(40), nullable=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))