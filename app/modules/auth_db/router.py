from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.core.database import SessionLocal, engine, Base
from app.modules.auth_db import models, schemas
from app.core.security import create_access_token, verify_password
import bcrypt

# Crear tablas
# Base.metadata.create_all(bind=engine)

router = APIRouter(prefix='/modules/auth_db', tags=['auth_db'])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post('/users', response_model=schemas.UserResponse)
def create_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail='Username already registered')
    
    pwd_bytes = user.password.encode('utf-8')
    hashed_password = bcrypt.hashpw(pwd_bytes, bcrypt.gensalt()).decode('utf-8')
    
    db_user = models.User(
        username=user.username,
        full_name=user.full_name,
        dni=user.dni,
        phone=user.phone,
        email=user.email,
        role=user.role,
        hashed_password=hashed_password
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

@router.post('/login')
async def login(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    username = body.get("username")
    password = body.get("password")
    
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail='Credenciales incorrectas')
    
    access_token = create_access_token(data={'sub': user.username, 'role': user.role.value})
    return {'access_token': access_token, 'token_type': 'bearer', 'user': {'email': user.username, 'role': user.role.value}}

@router.get("/me", response_model=schemas.UserResponse)
def get_me(username: str, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user

@router.patch("/users/{username}", response_model=schemas.UserResponse)
def update_user(username: str, user_update: schemas.UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.username == username).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    db_user.full_name = user_update.full_name
    db_user.dni = user_update.dni
    db_user.phone = user_update.phone
    db_user.email = user_update.email
    db.commit()
    db.refresh(db_user)
    return db_user
