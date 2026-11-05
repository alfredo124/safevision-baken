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

def frame_generator(camera_id, subscription=None, live=False):
    sequence = 0
    try:
        while True:
            frame_bytes, _, sequence = video_processor.get_frame(camera_id, after_sequence=sequence, live=live)
            if frame_bytes is None:
                break
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    finally:
        video_processor.unsubscribe(camera_id, subscription)

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
def video_feed(camera_id: int, token: str | None = Query(None), sync: bool = Query(True),
               db: Session = Depends(get_db)):
    """Video limpio. sync=true (por defecto): retrasado para coincidir con los recuadros.
    sync=false: en vivo, sin retraso; los recuadros llegan algo atrasados."""
    if not token:
        raise HTTPException(status_code=401, detail='Autenticación requerida')
    current_user = get_user_from_token(token)
    camera = get_camera_for_user(camera_id, db, current_user)

    try:
        subscription = video_processor.subscribe(
            camera.id,
            camera.stream_url,
            draw_boxes=camera.overlay_enabled,
            confidence_threshold=camera.confidence_threshold,
            processing_fps=camera.processing_fps,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return StreamingResponse(
        frame_generator(camera.id, subscription, live=not sync),
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

    live = websocket.query_params.get('sync') in ('0', 'false')
    await websocket.accept()
    # Mensajes del motor (Tarea 1C): uno por análisis, no por frame. Sin práctica
    # iniciada se envía un aviso cada 2 s para que el cliente borre los recuadros.
    last_sent, idle_since = None, 0.0
    try:
        while True:
            status = video_processor.monitoring_status(camera_id)
            overlay = video_processor.latest_overlay(camera_id, live=live)
            now = asyncio.get_running_loop().time()
            meta = {'sync': not live}
            if status:
                meta.update(sync_delay=0.0 if live else status['sync_delay'], analysis_rate=status['analysis_rate'],
                            started_at=status['started_at'], reconnecting=status.get('reconnecting', False))
            if status and status.get('reconnecting'):
                overlay = None  # sin señal: no se muestran recuadros antiguos
            if overlay is not None and overlay.get('analysis_id') != last_sent:
                await websocket.send_text(json.dumps({**overlay, **meta}))
                last_sent, idle_since = overlay.get('analysis_id'), now
            elif overlay is None and now - idle_since >= 1.0:
                # Sin práctica: aviso para borrar recuadros. Con práctica pero aún sin un
                # análisis que mostrar: "sincronizando" (no es "práctica no iniciada").
                await websocket.send_text(json.dumps({
                    'camera_id': camera_id, 'monitoring': bool(status), 'warming_up': bool(status),
                    'people': [], **meta,
                }))
                idle_since, last_sent = now, None
            await asyncio.sleep(0.1)
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
