import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import create_access_token, verify_password
from app.modules.auth_db import models, schemas
from app.modules.auth_db.dependencies import get_current_user, require_admin

router = APIRouter(prefix='/modules/auth_db', tags=['auth_db'])

ADMIN = models.UserRole.ADMINISTRADOR
# Rol de encargado según el tipo del laboratorio asignado.
ROLE_BY_LABORATORY = {'QUIMICA': models.UserRole.ENCARGADO_QUIMICA, 'FISICA': models.UserRole.ENCARGADO_FISICA}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def _is_active():
    # Usuarios antiguos tienen is_active NULL: cuentan como activos (igual que al validar el token).
    return or_(models.User.is_active.is_(True), models.User.is_active.is_(None))


def _active_admins(db: Session) -> int:
    return db.query(models.User).filter(models.User.role == ADMIN, _is_active()).count()


def _user_response(db: Session, user: models.User) -> dict:
    """Datos del usuario con el nombre de su laboratorio y de su local."""
    from app.modules.reglas_lab.models import Campus, Workshop  # otro módulo: se importa al usarlo
    workshop = db.get(Workshop, user.workshop_id) if user.workshop_id else None
    campus = db.get(Campus, workshop.campus_id) if workshop and workshop.campus_id else None
    return {
        'id': user.id, 'username': user.username, 'full_name': user.full_name, 'dni': user.dni,
        'phone': user.phone, 'email': user.email, 'role': user.role, 'is_active': user.is_active is not False,
        'created_at': user.created_at,
        'workshop_id': workshop.id if workshop else None, 'workshop_name': workshop.name if workshop else None,
        'laboratory_type': workshop.laboratory_type if workshop else None,
        'campus_id': campus.id if campus else None, 'campus_name': campus.name if campus else None,
    }


def _resolve_role(db: Session, role: models.UserRole, workshop_id: int | None) -> tuple[models.UserRole, int | None]:
    """El administrador general no tiene laboratorio. Un encargado necesita uno, y su rol
    (Química o Física) sale del tipo de ese laboratorio."""
    if role == ADMIN:
        return ADMIN, None
    if workshop_id is None:
        raise HTTPException(status_code=400, detail='Asigna un laboratorio al encargado')
    from app.modules.reglas_lab.models import Workshop
    workshop = db.get(Workshop, workshop_id)
    if workshop is None or not workshop.is_active:
        raise HTTPException(status_code=400, detail='El laboratorio asignado no existe')
    return ROLE_BY_LABORATORY[workshop.laboratory_type], workshop.id


# --- Configuración inicial: solo mientras no exista un administrador ---

@router.get('/setup', response_model=schemas.SetupStatus)
def setup_status(db: Session = Depends(get_db)):
    """Pública: la web muestra la pantalla de configuración inicial si todavía no hay administrador."""
    return {'needs_setup': _active_admins(db) == 0}


@router.post('/setup', status_code=status.HTTP_201_CREATED)
def setup_first_admin(payload: schemas.SetupCreate, db: Session = Depends(get_db)):
    """Crea el administrador general la primera vez que se usa el sistema. Después queda cerrada:
    los demás usuarios los crea ese administrador."""
    if _active_admins(db) > 0:
        raise HTTPException(status_code=409, detail='El sistema ya tiene un administrador. Inicia sesión.')
    if db.query(models.User).filter(models.User.username == payload.username).first():
        raise HTTPException(status_code=400, detail='Ese nombre de usuario ya está registrado')
    user = models.User(
        username=payload.username, full_name=payload.full_name.strip(), dni=payload.dni, phone=payload.phone,
        email=payload.email, role=ADMIN, is_active=True, hashed_password=_hash(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _login_payload(db, user)


# --- Sesión ---

def _login_payload(db: Session, user: models.User) -> dict:
    token = create_access_token(data={'sub': user.username, 'role': user.role.value})
    data = _user_response(db, user)
    return {'access_token': token, 'token_type': 'bearer',
            'user': {'email': user.username, 'role': user.role.value, 'full_name': user.full_name,
                     'campus_name': data['campus_name'], 'workshop_name': data['workshop_name']}}


@router.post('/login')
async def login(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    username = body.get('username')
    password = body.get('password')

    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail='Credenciales incorrectas')
    if user.is_active is False:
        raise HTTPException(status_code=401, detail='Usuario desactivado. Consulta al administrador.')
    return _login_payload(db, user)


@router.get('/me', response_model=schemas.UserResponse)
def get_me(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return _user_response(db, current_user)


# --- Gestión de usuarios (solo el administrador general) ---

@router.get('/users', response_model=list[schemas.UserResponse])
def list_users(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    users = db.query(models.User).order_by(models.User.role, models.User.full_name).all()
    return [_user_response(db, user) for user in users]


@router.post('/users', response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(user: schemas.UserCreate, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    if db.query(models.User).filter(models.User.username == user.username).first():
        raise HTTPException(status_code=400, detail='Ese nombre de usuario ya está registrado')
    role, workshop_id = _resolve_role(db, user.role, user.workshop_id)
    db_user = models.User(
        username=user.username, full_name=user.full_name.strip(), dni=user.dni, phone=user.phone,
        email=user.email, role=role, workshop_id=workshop_id, is_active=True, hashed_password=_hash(user.password),
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return _user_response(db, db_user)


@router.patch('/users/{username}', response_model=schemas.UserResponse)
def update_user(
    username: str,
    user_update: schemas.UserUpdate,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    db_user = db.query(models.User).filter(models.User.username == username).first()
    if not db_user:
        raise HTTPException(status_code=404, detail='Usuario no encontrado')

    changes = user_update.model_dump(exclude_unset=True)
    password = changes.pop('password', None)
    if 'role' in changes or 'workshop_id' in changes:
        role, workshop_id = _resolve_role(db, changes.pop('role', None) or db_user.role,
                                          changes.pop('workshop_id', db_user.workshop_id))
        changes.update(role=role, workshop_id=workshop_id)

    is_self = db_user.id == admin.id
    loses_admin = db_user.role == ADMIN and (changes.get('role', ADMIN) != ADMIN or changes.get('is_active') is False)
    if is_self and loses_admin:
        raise HTTPException(status_code=400, detail='No puedes quitarte el rol de administrador ni desactivarte')
    if loses_admin and db_user.is_active is not False and _active_admins(db) <= 1:
        raise HTTPException(status_code=400, detail='Debe quedar al menos un administrador activo')

    for field, value in changes.items():
        setattr(db_user, field, value)
    if password:
        db_user.hashed_password = _hash(password)
    db.commit()
    db.refresh(db_user)
    return _user_response(db, db_user)
