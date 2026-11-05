from pydantic import BaseModel, ConfigDict, Field
from typing import Literal, Optional
from datetime import datetime

class CameraBase(BaseModel):
    name: str
    laboratory_type: Literal['QUIMICA', 'FISICA']
    stream_url: str
    output_stream_url: Optional[str] = None
    ip_address: Optional[str] = None
    confidence_threshold: float = Field(default=0.5, ge=0, le=1)
    processing_fps: int = Field(default=10, ge=1, le=60)
    is_active: bool = True
    overlay_enabled: bool = True

class CameraCreate(CameraBase):
    username: Optional[str] = None
    password: Optional[str] = None

class CameraResponse(CameraBase):
    id: int
    source_type: Literal['STREAM', 'BROWSER'] = 'STREAM'
    station_connected: bool = False  # cámara de navegador que ya transmite una estación
    created_at: datetime
    updated_at: datetime
    status: str

    model_config = ConfigDict(from_attributes=True)


class AvailableCameraResponse(BaseModel):
    device_index: int
    name: str
    stream_url: str
    width: int
    height: int


class BrowserCameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    laboratory_type: Literal['QUIMICA', 'FISICA']
