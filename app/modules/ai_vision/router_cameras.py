from pathlib import Path
from uuid import uuid4

import cv2
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
MAX_LOCAL_CAMERA_INDEX = 5

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


@router.post('/cameras/browser', response_model=schemas.CameraResponse, status_code=201)
def create_browser_camera(
    payload: schemas.BrowserCameraCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Cámara alimentada por una "estación de cámara" del navegador (celular o laptop)."""
    from app.modules.ai_vision.browser_ingest import BROWSER_PREFIX
    authorize_camera_creation(current_user, payload.laboratory_type)
    camera = models.Camera(
        name=payload.name.strip(),
        laboratory_type=payload.laboratory_type,
        stream_url=f'{BROWSER_PREFIX}{uuid4().hex}',
        source_type='BROWSER',
        status='offline',
    )
    db.add(camera)
    db.commit()
    db.refresh(camera)
    return camera


@router.get('/cameras/available', response_model=list[schemas.AvailableCameraResponse])
def get_available_cameras(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    registered_sources = {
        camera.stream_url
        for camera in db.query(models.Camera.stream_url).filter(models.Camera.is_active.is_(True)).all()
    }
    available = []
    for device_index in range(MAX_LOCAL_CAMERA_INDEX + 1):
        source_url = str(device_index)
        if source_url in registered_sources:
            continue

        capture = cv2.VideoCapture(device_index)
        try:
            if not capture.isOpened():
                continue
            success, _ = capture.read()
            if not success:
                continue
            available.append({
                'device_index': device_index,
                'name': f'Cámara conectada {device_index}',
                'stream_url': source_url,
                'width': int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                'height': int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            })
        finally:
            capture.release()
    return available


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


def remove_video_file(path: Path, db: Session) -> None:
    """Borra el video subido de una cámara eliminada. Si otra cámara activa usa el mismo archivo, se deja.
    En Windows el archivo puede seguir abierto unos segundos por la captura que se está cerrando: se
    reintenta en segundo plano en vez de responder con error (la cámara ya quedó eliminada)."""
    import logging
    import threading
    import time

    if db.query(models.Camera.id).filter(models.Camera.stream_url == str(path), models.Camera.is_active.is_(True)).first():
        return

    def attempt():
        for _ in range(20):
            try:
                path.unlink(missing_ok=True)
                return
            except PermissionError:
                time.sleep(0.5)
        logging.getLogger(__name__).warning('No se pudo borrar el video %s (sigue en uso)', path)

    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        threading.Thread(target=attempt, daemon=True).start()


@router.delete('/cameras/{camera_id}', status_code=204)
def delete_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    camera = db.query(models.Camera).filter(models.Camera.id == camera_id, models.Camera.is_active.is_(True)).first()
    if not camera:
        raise HTTPException(status_code=404, detail='Cámara no encontrada')

    authorize_camera_creation(current_user, camera.laboratory_type)
    from app.modules.reglas_lab.models import Incident, WorkshopCamera

    has_history = db.query(Incident.id).filter_by(camera_id=camera.id).first() is not None

    stored_video_path = Path(camera.stream_url)
    try:
        stored_video_path.resolve().relative_to(VIDEO_UPLOAD_DIR.resolve())
    except ValueError:
        stored_video_path = None

    db.query(WorkshopCamera).filter_by(camera_id=camera.id).delete(synchronize_session=False)
    video_processor.release_camera(camera.id)
    if has_history:
        # El historial conserva sus propios datos (nombre, taller, laboratorio). De la cámara se borra la
        # fuente y las credenciales; solo queda una fila oculta para que su número no se reutilice
        # (SQLite reutiliza el último id) y no se mezcle con el historial de otra cámara.
        camera.is_active = False
        camera.status = 'offline'
        camera.stream_url = f'eliminada:{camera.id}'
        camera.output_stream_url = camera.ip_address = camera.username = camera.password = None
    else:
        db.delete(camera)
    db.commit()
    if stored_video_path:
        remove_video_file(stored_video_path, db)
