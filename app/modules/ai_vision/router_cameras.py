from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import SessionLocal, engine, Base
from app.modules.ai_vision import models, schemas
from app.modules.auth_db.dependencies import get_current_user

# Crear tablas
Base.metadata.create_all(bind=engine)

router = APIRouter(prefix='/modules/ai_vision', tags=['ai_vision'])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post('/cameras', response_model=schemas.CameraResponse)
def create_camera(camera: schemas.CameraCreate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    if current_user.role.value != 'ADMINISTRADOR':
        raise HTTPException(status_code=403, detail='Solo administradores pueden registrar cámaras')
    
    db_camera = models.Camera(**camera.dict())
    db.add(db_camera)
    db.commit()
    db.refresh(db_camera)
    return db_camera
