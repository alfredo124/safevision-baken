import cv2
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import asyncio
import json
from app.core.database import SessionLocal
from app.modules.ai_vision import models, schemas
from app.modules.auth_db.dependencies import get_current_user
from app.modules.ai_vision.services import video_processor

router = APIRouter(prefix='/modules/ai_vision', tags=['ai_vision'])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def frame_generator(camera):
    while True:
        # Usamos el campo overlay_enabled para decidir si dibujar o no
        frame_bytes, _ = video_processor.get_frame(camera.id, camera.stream_url, draw_boxes=camera.overlay_enabled)
        if frame_bytes is None:
            break
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@router.get('/video_feed/{camera_id}')
def video_feed(camera_id: int, db: Session = Depends(get_db)):
    camera = db.query(models.Camera).filter(models.Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    
    return StreamingResponse(
        frame_generator(camera),
        media_type='multipart/x-mixed-replace; boundary=frame'
    )

@router.get('/video_feed_raw/{camera_id}')
def video_feed_raw(camera_id: int, db: Session = Depends(get_db)):
    camera = db.query(models.Camera).filter(models.Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    
    return StreamingResponse(
        frame_generator_raw(camera_id, camera.stream_url),
        media_type='multipart/x-mixed-replace; boundary=frame'
    )

def frame_generator_raw(camera_id, stream_url):
    source = int(stream_url) if stream_url.isdigit() else stream_url
    cap = cv2.VideoCapture(source)
    while True:
        success, frame = cap.read()
        if not success:
            if not stream_url.isdigit():
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            break
        ret, buffer = cv2.imencode('.jpg', frame)
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    cap.release()

@router.websocket("/ws/detections/{camera_id}")
async def websocket_detections(websocket: WebSocket, camera_id: int):
    await websocket.accept()
    try:
        while True:
            detections = video_processor.latest_detections.get(camera_id, [])
            await websocket.send_text(json.dumps(detections))
            await asyncio.sleep(0.03)
    except Exception:
        # Silenciar errores si el cliente se desconecta
        pass

@router.get('/cameras', response_model=list[schemas.CameraResponse])
def get_cameras(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    query = db.query(models.Camera).filter(models.Camera.is_active == True)
    if current_user.role.value == 'ENCARGADO_QUIMICA':
        query = query.filter(models.Camera.laboratory_type == 'QUIMICA')
    elif current_user.role.value == 'ENCARGADO_FISICA':
        query = query.filter(models.Camera.laboratory_type == 'FISICA')
    
    return query.all()
