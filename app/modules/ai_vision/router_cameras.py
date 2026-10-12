from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.modules.ai_vision import models, schemas
from app.modules.auth_db.dependencies import get_current_user
from app.modules.ai_vision.services import video_processor

router = APIRouter(prefix='/modules/ai_vision', tags=['ai_vision'])
PROJECT_ROOT = Path(__file__).resolve().parents[3]
VIDEO_UPLOAD_DIR = PROJECT_ROOT / 'videos' / 'cameras'
MAX_VIDEO_SIZE = 500 * 1024 * 1024
ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def authorize_camera_creation(current_user, laboratory_type: str):
    role_to_laboratory = {
        'ENCARGADO_QUIMICA': 'QUIMICA',
        'ENCARGADO_FISICA': 'FISICA',
    }
    role = current_user.role.value
    if role != 'ADMINISTRADOR' and role not in role_to_laboratory:
        raise HTTPException(status_code=403, detail='No tienes permiso para registrar cámaras')
    if role in role_to_laboratory and laboratory_type != role_to_laboratory[role]:
        raise HTTPException(status_code=403, detail='No puedes registrar cámaras para otra área')


@router.post('/cameras', response_model=schemas.CameraResponse)
def create_camera(camera: schemas.CameraCreate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    authorize_camera_creation(current_user, camera.laboratory_type)
    db_camera = models.Camera(**camera.model_dump(), status='offline')
    db.add(db_camera)
    db.commit()
    db.refresh(db_camera)
    return db_camera


@router.post('/cameras/upload', response_model=schemas.CameraResponse, status_code=201)
async def create_camera_with_video(
    name: str = Form(..., min_length=1, max_length=120),
    laboratory_type: str = Form(..., pattern='^(QUIMICA|FISICA)$'),
    stream_url: str | None = Form(None),
    video: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    authorize_camera_creation(current_user, laboratory_type)
    if bool(stream_url and stream_url.strip()) == bool(video):
        raise HTTPException(status_code=400, detail='Indica una URL de cámara o carga un video, no ambos')

    stored_video_path = None
    if video:
        extension = Path(video.filename or '').suffix.lower()
        if extension not in ALLOWED_VIDEO_EXTENSIONS:
            raise HTTPException(status_code=400, detail='Formato no permitido. Usa MP4, AVI, MOV, MKV o WEBM')

        VIDEO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        stored_video_path = VIDEO_UPLOAD_DIR / f'{uuid4().hex}{extension}'
        total_size = 0
        try:
            with stored_video_path.open('wb') as destination:
                while chunk := await video.read(1024 * 1024):
                    total_size += len(chunk)
                    if total_size > MAX_VIDEO_SIZE:
                        raise HTTPException(status_code=413, detail='El video supera el límite de 500 MB')
                    destination.write(chunk)
            if total_size == 0:
                raise HTTPException(status_code=400, detail='El archivo de video está vacío')
            camera_stream_url = str(stored_video_path.resolve()).replace('\\', '/')
        except Exception:
            stored_video_path.unlink(missing_ok=True)
            raise
    else:
        camera_stream_url = stream_url.strip()

    db_camera = models.Camera(
        name=name.strip(),
        laboratory_type=laboratory_type,
        stream_url=camera_stream_url,
        status='offline',
    )
    try:
        db.add(db_camera)
        db.commit()
        db.refresh(db_camera)
    except Exception:
        db.rollback()
        if stored_video_path:
            stored_video_path.unlink(missing_ok=True)
        raise
    return db_camera


@router.delete('/cameras/{camera_id}', status_code=204)
def delete_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    camera = db.query(models.Camera).filter(models.Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail='Cámara no encontrada')

    authorize_camera_creation(current_user, camera.laboratory_type)
    from app.modules.reglas_lab.models import Incident, WorkshopCamera

    if db.query(Incident.id).filter_by(camera_id=camera.id).first():
        raise HTTPException(status_code=409, detail='No se puede eliminar una cámara con incidencias históricas')

    stored_video_path = Path(camera.stream_url)
    try:
        stored_video_path.resolve().relative_to(VIDEO_UPLOAD_DIR.resolve())
    except ValueError:
        stored_video_path = None

    db.query(WorkshopCamera).filter_by(camera_id=camera.id).delete(synchronize_session=False)
    video_processor.release_camera(camera.id)
    db.delete(camera)
    db.commit()
    if stored_video_path:
        stored_video_path.unlink(missing_ok=True)
