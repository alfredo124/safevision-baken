from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.modules.auth_db.models import UserRole

class UserBase(BaseModel):
    username: str
    full_name: str
    dni: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    role: UserRole

class UserCreate(UserBase):
    password: str

class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class LoginRequest(BaseModel):
    username: str
    password: str
