from fastapi import APIRouter, Depends, HTTPException, status
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

@router.get('/cameras', response_model=list[schemas.CameraResponse])
def get_cameras(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    query = db.query(models.Camera).filter(models.Camera.is_active == True)
    
    # Restricción por rol
    if current_user.role.value == 'ENCARGADO_QUIMICA':
        query = query.filter(models.Camera.laboratory_type == 'QUIMICA')
    elif current_user.role.value == 'ENCARGADO_FISICA':
        query = query.filter(models.Camera.laboratory_type == 'FISICA')
    
    return query.all()
