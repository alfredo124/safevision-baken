"""Inicia y detiene el monitoreo continuo de un taller (una práctica activa).

El monitoreo no depende de que alguien mire el video: cada cámara asignada al taller
se analiza con su propio ComplianceEngine y cada ViolationEvent crea una incidencia
PENDIENTE con su captura enmascarada.
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.modules.ai_vision.capture import save_incident_capture
from app.modules.ai_vision.models import Camera
from app.modules.ai_vision.services import video_processor as default_processor
from app.modules.reglas_lab import models
from app.modules.reglas_lab.incidencias.servicio import create_system_incident, rules_for_camera

logger = logging.getLogger(__name__)

# Talleres con la práctica en curso. Se guarda aparte de las cámaras: una práctica puede iniciarse
# sin cámaras y recibirlas después. Vive en memoria, igual que el análisis (se pierde al reiniciar).
_active_workshops: set[int] = set()
_active_lock = threading.Lock()


def is_active(workshop_id: int) -> bool:
    with _active_lock:
        return workshop_id in _active_workshops


def _event_handler(workshop_id: int, session_factory=SessionLocal, capture_writer=save_incident_capture):
    def handle(camera_id, event, frame, observation):
        def save_capture(code: str):
            return capture_writer(frame, observation.persons, code, highlight_box=event.person_box)

        with session_factory() as db:
            incident = create_system_incident(db, camera_id, workshop_id, event, observation.frame_size, save_capture)
            logger.info('Incidencia %s creada (cámara %s, %s).', incident.code, camera_id, event.equipment_id)
    return handle


def workshop_cameras(db: Session, workshop_id: int) -> list[Camera]:
    return (
        db.query(Camera)
        .join(models.WorkshopCamera, models.WorkshopCamera.camera_id == Camera.id)
        .filter(models.WorkshopCamera.workshop_id == workshop_id, Camera.is_active.is_(True))
        .order_by(Camera.id)
        .all()
    )


def start_workshop(db: Session, workshop_id: int, processor=None, session_factory=SessionLocal,
                   capture_writer=save_incident_capture) -> list[dict]:
    processor = processor or default_processor
    with _active_lock:
        _active_workshops.add(workshop_id)
    results = []
    for camera in workshop_cameras(db, workshop_id):
        camera_rules = rules_for_camera(db, camera.id)
        detectable = [rule for rule in camera_rules.rules if rule.model_class] if camera_rules else []
        if not detectable:
            results.append({'camera_id': camera.id, 'started': False,
                            'detail': 'El taller no tiene EPP detectables configurados'})
            continue
        try:
            started = processor.start_monitoring(
                camera.id, camera.stream_url, list(camera_rules.rules),
                _event_handler(workshop_id, session_factory, capture_writer),
                confidence_threshold=camera.confidence_threshold,
                processing_fps=camera.processing_fps,
                workshop_id=workshop_id,
            )
        except RuntimeError as error:
            camera.status = 'offline'
            results.append({'camera_id': camera.id, 'started': False, 'detail': str(error)})
            continue
        camera.status = 'online'
        results.append({'camera_id': camera.id, 'started': True,
                        'detail': 'Monitoreo iniciado' if started else 'Ya estaba en monitoreo'})
    db.commit()
    return results


def stop_workshop(db: Session, workshop_id: int, processor=None) -> list[dict]:
    processor = processor or default_processor
    with _active_lock:
        _active_workshops.discard(workshop_id)
    results = []
    for camera in workshop_cameras(db, workshop_id):
        stopped = processor.stop_monitoring(camera.id)
        results.append({'camera_id': camera.id, 'stopped': stopped})
    return results


def sync_workshop(db: Session, workshop_id: int, previous_camera_ids: set[int], processor=None,
                  session_factory=SessionLocal, capture_writer=save_incident_capture) -> list[dict]:
    """Tras guardar la configuración del taller con la práctica en curso: las cámaras nuevas empiezan a
    analizarse, las quitadas dejan de hacerlo y las que siguen usan las reglas nuevas."""
    if not is_active(workshop_id):
        return []
    processor = processor or default_processor
    current = {camera.id for camera in workshop_cameras(db, workshop_id)}
    results = []
    for camera_id in sorted(previous_camera_ids - current):
        results.append({'camera_id': camera_id, 'stopped': processor.stop_monitoring(camera_id),
                        'detail': 'Quitada del taller: deja de analizarse'})
    for camera_id in sorted(current & previous_camera_ids):
        camera_rules = rules_for_camera(db, camera_id)
        if camera_rules and processor.update_monitoring_rules(camera_id, list(camera_rules.rules)):
            results.append({'camera_id': camera_id, 'detail': 'Reglas actualizadas'})
    # Las nuevas (y las que no estuvieran corriendo) se inician; las que ya corren no se tocan.
    results += [item for item in start_workshop(db, workshop_id, processor, session_factory, capture_writer)
                if item.get('detail') != 'Ya estaba en monitoreo']
    return results


def workshop_status(db: Session, workshop_id: int, processor=None) -> list[dict]:
    processor = processor or default_processor
    return [
        processor.monitoring_status(camera.id) or {'camera_id': camera.id, 'running': False}
        for camera in workshop_cameras(db, workshop_id)
    ]
