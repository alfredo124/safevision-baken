from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import enum
from app.core.database import Base

class UserRole(str, enum.Enum):
    ADMINISTRADOR = 'ADMINISTRADOR'
    ENCARGADO_QUIMICA = 'ENCARGADO_QUIMICA'
    ENCARGADO_FISICA = 'ENCARGADO_FISICA'

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    dni = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    role = Column(Enum(UserRole), default=UserRole.ENCARGADO_QUIMICA)
    # Laboratorio asignado (lab_workshops.id); su local es el del laboratorio. None para el administrador
    # general. Sin ForeignKey declarada: la tabla de laboratorios vive en otro módulo.
    workshop_id = Column(Integer, nullable=True, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class ActivityLog(Base):
    """Bitácora: quién hizo qué, cuándo y desde qué equipo (ver app/core/bitacora.py)."""
    __tablename__ = 'activity_logs'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'))  # None: intento de acceso sin usuario válido
    # Nombre del usuario en ese momento: la bitácora no cambia si después se renombra.
    user_name = Column(String(120), nullable=True)
    action = Column(String, nullable=False)  # código corto, p. ej. CONFIRMAR_ALERTA
    module_affected = Column(String, nullable=False)  # usuarios, locales, laboratorios, camaras, ...
    description = Column(String(500), nullable=True)  # texto legible: qué se cambió
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    ip_address = Column(String, nullable=True)
