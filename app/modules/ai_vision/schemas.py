from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class CameraBase(BaseModel):
    name: str
    laboratory_type: str
    stream_url: str
    is_active: bool = True

class CameraCreate(CameraBase):
    pass

class CameraResponse(CameraBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
