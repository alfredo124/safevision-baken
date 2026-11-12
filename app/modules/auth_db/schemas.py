from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.modules.auth_db.models import UserRole

# Usuario: letras, números, punto, guion y guion bajo (se usa para iniciar sesión).
USERNAME = Field(min_length=3, max_length=40, pattern=r'^[A-Za-z0-9._-]+$')
PASSWORD = Field(min_length=8, max_length=72)  # bcrypt solo usa los primeros 72 bytes
DNI = Field(default=None, pattern=r'^\d{8}$')
PHONE = Field(default=None, max_length=20)
EMAIL = Field(default=None, max_length=120, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class UserBase(BaseModel):
    username: str = USERNAME
    full_name: str = Field(min_length=3, max_length=120)
    dni: Optional[str] = DNI
    phone: Optional[str] = PHONE
    email: Optional[str] = EMAIL
    role: UserRole


class UserCreate(UserBase):
    password: str = PASSWORD
    # Obligatorio para los encargados: su laboratorio define su área y su local.
    workshop_id: Optional[int] = None


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=3, max_length=120)
    dni: Optional[str] = DNI
    phone: Optional[str] = PHONE
    email: Optional[str] = EMAIL
    role: Optional[UserRole] = None
    workshop_id: Optional[int] = None
    is_active: Optional[bool] = None
    # Nueva contraseña, si el administrador la restablece.
    password: Optional[str] = Field(default=None, min_length=8, max_length=72)


class UserResponse(BaseModel):
    # Sin las validaciones de entrada: usuarios antiguos pueden tener datos en otro formato.
    username: str
    full_name: str
    dni: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    role: UserRole
    id: int
    is_active: bool
    created_at: Optional[datetime] = None  # vacío en usuarios creados por scripts antiguos
    workshop_id: Optional[int] = None
    workshop_name: Optional[str] = None
    laboratory_type: Optional[str] = None
    campus_id: Optional[int] = None
    campus_name: Optional[str] = None

    class Config:
        from_attributes = True


class SetupStatus(BaseModel):
    needs_setup: bool


class SetupCreate(BaseModel):
    """Primer administrador general, creado desde la pantalla de configuración inicial."""
    username: str = USERNAME
    password: str = PASSWORD
    full_name: str = Field(min_length=3, max_length=120)
    dni: Optional[str] = DNI
    phone: Optional[str] = PHONE
    email: Optional[str] = EMAIL


class Token(BaseModel):
    access_token: str
    token_type: str


class LoginRequest(BaseModel):
    username: str
    password: str
