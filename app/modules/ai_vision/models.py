from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime
from app.core.database import Base

class Camera(Base):
    __tablename__ = 'cameras'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    laboratory_type = Column(String, nullable=False) # QUIMICA o FISICA
    stream_url = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
