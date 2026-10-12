import cv2
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import asyncio
import json
from app.core.database import SessionLocal
from app.modules.ai_vision import models, schemas
from app.modules.auth_db.dependencies import get_current_user, get_user_from_token
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
        frame_bytes, _ = video_processor.get_frame(
            camera.id,
            camera.stream_url,
            draw_boxes=camera.overlay_enabled,
            confidence_threshold=camera.confidence_threshold,
            processing_fps=camera.processing_fps,
        )
        if frame_bytes is None:
            break
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

def get_camera_for_user(camera_id: int, db: Session, current_user):
    camera = db.query(models.Camera).filter(models.Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail='Cámara no encontrada')
    role = current_user.role.value
    if role == 'ENCARGADO_QUIMICA' and camera.laboratory_type != 'QUIMICA':
        raise HTTPException(status_code=403, detail='No tienes acceso a esta cámara')
    if role == 'ENCARGADO_FISICA' and camera.laboratory_type != 'FISICA':
        raise HTTPException(status_code=403, detail='No tienes acceso a esta cámara')
    return camera


@router.get('/video_feed/{camera_id}')
def video_feed(camera_id: int, token: str | None = Query(None), db: Session = Depends(get_db)):
    if not token:
        raise HTTPException(status_code=401, detail='Autenticación requerida')
    current_user = get_user_from_token(token)
    camera = get_camera_for_user(camera_id, db, current_user)
    
    return StreamingResponse(
        frame_generator(camera),
        media_type='multipart/x-mixed-replace; boundary=frame'
    )

@router.get('/video_feed_raw/{camera_id}')
def video_feed_raw(camera_id: int, token: str | None = Query(None), db: Session = Depends(get_db)):
    if not token:
        raise HTTPException(status_code=401, detail='Autenticación requerida')
    current_user = get_user_from_token(token)
    camera = get_camera_for_user(camera_id, db, current_user)
    
    return StreamingResponse(
        frame_generator_raw(camera_id, camera.stream_url),
        media_type='multipart/x-mixed-replace; boundary=frame'
    )

def frame_generator_raw(camera_id, stream_url):
    source = int(stream_url) if stream_url.isdigit() else stream_url
    cap = cv2.VideoCapture(source)
    try:
        while True:
            success, frame = cap.read()
            if not success:
                if not stream_url.isdigit():
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                break
            ret, buffer = cv2.imencode('.jpg', frame)
            if ret:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
    finally:
        cap.release()

@router.websocket("/ws/detections/{camera_id}")
async def websocket_detections(websocket: WebSocket, camera_id: int):
    token = websocket.query_params.get('token')
    if not token:
        await websocket.close(code=4401)
        return
    try:
        current_user = get_user_from_token(token)
        db = SessionLocal()
        try:
            get_camera_for_user(camera_id, db, current_user)
        finally:
            db.close()
    except HTTPException as exc:
        await websocket.close(code=4403 if exc.status_code == 403 else 4401)
        return

    await websocket.accept()
    try:
        while True:
            detections = video_processor.latest_detections.get(camera_id, [])
            await websocket.send_text(json.dumps(detections))
            await asyncio.sleep(0.03)
    except WebSocketDisconnect:
        pass

@router.get('/cameras', response_model=list[schemas.CameraResponse])
def get_cameras(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    query = db.query(models.Camera).filter(models.Camera.is_active == True)
    if current_user.role.value == 'ENCARGADO_QUIMICA':
        query = query.filter(models.Camera.laboratory_type == 'QUIMICA')
    elif current_user.role.value == 'ENCARGADO_FISICA':
        query = query.filter(models.Camera.laboratory_type == 'FISICA')
    
    return query.all()
