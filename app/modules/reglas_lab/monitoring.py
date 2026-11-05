"""Inicia y detiene el monitoreo continuo de un taller (una práctica activa).

El monitoreo no depende de que alguien mire el video: cada cámara asignada al taller
se analiza con su propio ComplianceEngine y cada ViolationEvent crea una incidencia
PENDIENTE con su captura enmascarada.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.modules.ai_vision.capture import save_incident_capture
from app.modules.ai_vision.models import Camera
from app.modules.ai_vision.services import video_processor as default_processor
from app.modules.reglas_lab import models
from app.modules.reglas_lab.incident_service import create_system_incident, rules_for_camera

logger = logging.getLogger(__name__)


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
    results = []
    for camera in workshop_cameras(db, workshop_id):
        stopped = processor.stop_monitoring(camera.id)
        results.append({'camera_id': camera.id, 'stopped': stopped})
    return results


def workshop_status(db: Session, workshop_id: int, processor=None) -> list[dict]:
    processor = processor or default_processor
    return [
        processor.monitoring_status(camera.id) or {'camera_id': camera.id, 'running': False}
        for camera in workshop_cameras(db, workshop_id)
    ]
