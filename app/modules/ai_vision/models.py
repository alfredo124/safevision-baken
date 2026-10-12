from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, func
from datetime import datetime
from app.core.database import Base

class Camera(Base):
    __tablename__ = 'cameras'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    laboratory_type = Column(String, nullable=False) # QUIMICA o FISICA
    stream_url = Column(String, nullable=False)
    output_stream_url = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)goo
    username = Column(String, nullable=True)
    password = Column(String, nullable=True)
    confidence_threshold = Column(Float, nullable=False, default=0.5, server_default='0.5')
    processing_fps = Column(Integer, nullable=False, default=10, server_default='20')
    is_active = Column(Boolean, default=True)
    overlay_enabled = Column(Boolean, default=True)
    status = Column(String, nullable=False, default='offline', server_default='offline')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow,
                         server_default=func.current_timestamp())
