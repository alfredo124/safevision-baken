"""Acceso a datos del monitoreo: reglas vigentes de una cámara e incidencias del sistema.

Todo el SQL del flujo automático vive aquí, separado del ciclo de video, para poder
cambiar el almacenamiento más adelante sin tocar la detección.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core import events
from app.modules.ai_vision.models import Camera
from app.modules.reglas_lab import models
from app.modules.reglas_lab.compliance import DETAIL_COAT_OPEN, DETAIL_GOGGLES_OFF, EquipmentRule, ViolationEvent

# Texto de una falta según su detalle: el EPP falta o está mal puesto.
DETAIL_TEXT = {DETAIL_COAT_OPEN: 'abierta (sin abotonar)', DETAIL_GOGGLES_OFF: 'fuera de los ojos'}


@dataclass(frozen=True)
class CameraRules:
    workshop_id: int
    workshop_name: str
    rules: tuple[EquipmentRule, ...]


def rules_for_camera(db: Session, camera_id: int) -> CameraRules | None:
    """Reglas vigentes del taller al que está asignada la cámara, o None si no tiene taller."""
    row = (
        db.query(models.Workshop)
        .join(models.WorkshopCamera, models.WorkshopCamera.workshop_id == models.Workshop.id)
        .filter(models.WorkshopCamera.camera_id == camera_id, models.Workshop.is_active.is_(True))
        .first()
    )
    if not row:
        return None
    rules = (
        db.query(models.WorkshopEquipmentRule, models.SafetyEquipment)
        .join(models.SafetyEquipment, models.SafetyEquipment.id == models.WorkshopEquipmentRule.equipment_id)
        .filter(models.WorkshopEquipmentRule.workshop_id == row.id,
                models.WorkshopEquipmentRule.is_required.is_(True),
                models.SafetyEquipment.is_active.is_(True))
        .order_by(models.SafetyEquipment.id)
        .all()
    )
    return CameraRules(row.id, row.name, tuple(
        EquipmentRule(
            equipment_id=equipment.id,
            slot=equipment.slot,
            model_class=equipment.model_class,
            severity=rule.severity or equipment.impact,
            rule_code=rule.rule_code or equipment.missing_code,
        )
        for rule, equipment in rules
    ))


def new_incident_code(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f'INC-{now:%Y%m%d}-{uuid4().hex[:8].upper()}'


SEVERITY_ORDER = {'CRÍTICO': 3, 'ALTO': 2, 'MEDIO': 1}
# Faltas de la misma persona separadas por menos de esto forman una sola alerta.
GROUP_SECONDS = 30.0


def _iou(a, b) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - width * height
    return width * height / union if union > 0 else 0.0


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def incident_faults(db: Session, incident: models.Incident) -> list[models.IncidentFault]:
    return (db.query(models.IncidentFault).filter_by(incident_id=incident.id)
            .order_by(models.IncidentFault.detected_at, models.IncidentFault.id).all())


def _refresh_summary(db: Session, incident: models.Incident) -> None:
    """Título, gravedad y descripción de la alerta a partir de todas sus faltas."""
    faults = incident_faults(db, incident)
    names = {
        row.id: row.name for row in db.query(models.SafetyEquipment)
        .filter(models.SafetyEquipment.id.in_([f.equipment_id for f in faults if f.equipment_id])).all()
    }
    labels = [names.get(f.equipment_id, f.equipment_id or 'EPP') for f in faults]
    codes = ', '.join(f.rule_code for f in faults if f.rule_code) or '-'
    missing = [label for f, label in zip(faults, labels) if f.detail not in DETAIL_TEXT]
    wrong = [f'{label} {DETAIL_TEXT[f.detail]}' for f, label in zip(faults, labels) if f.detail in DETAIL_TEXT]
    if len(faults) == 1:
        incident.title = (f'{labels[0]}: {DETAIL_TEXT[faults[0].detail]}' if wrong
                          else f'{labels[0]}: no se detecta')[:160]
    else:
        incident.title = f'Faltan {len(labels)} EPP: {", ".join(missing + wrong)}'[:160]
    incident.severity = max((f.severity for f in faults), key=lambda value: SEVERITY_ORDER.get(value, 0))
    found = []
    if missing:
        found.append(f'el sistema no detectó {", ".join(label.lower() for label in missing)}')
    if wrong:
        found.append(f'detectó {", ".join(text.lower() for text in wrong)}')
    incident.description = (
        f'Reglas {codes}: {" y ".join(found)} en zonas visibles de la misma persona. '
        f'Pendiente de validación del encargado.'
    )[:1000]


def _open_alert_for(db: Session, camera_id: int, event: ViolationEvent, now: datetime,
                    group_seconds: float, session_id: int | None) -> models.Incident | None:
    """Alerta pendiente de la misma persona con actividad reciente, en la misma práctica, si existe."""
    candidates = (
        db.query(models.Incident)
        .filter(models.Incident.camera_id == camera_id, models.Incident.status == 'PENDIENTE',
                models.Incident.created_by.is_(None), models.Incident.session_id.is_(session_id)
                if session_id is None else models.Incident.session_id == session_id)
        .order_by(models.Incident.id.desc()).limit(20).all()
    )
    for incident in candidates:
        faults = incident_faults(db, incident)
        last = max((_as_utc(f.detected_at) for f in faults), default=_as_utc(incident.occurred_at))
        if (now - last).total_seconds() > group_seconds:
            continue
        same_track = incident.track_id is not None and incident.track_id == event.track_id
        same_box = bool(incident.bounding_box) and _iou(incident.bounding_box, event.person_box) >= 0.3
        if same_track or same_box:
            return incident
    return None


def record_system_fault(
    db: Session,
    camera_id: int,
    workshop_id: int,
    event: ViolationEvent,
    frame_size: tuple[int, int] | None,
    save_capture: Callable[[str], str | None] | None = None,
    group_seconds: float | None = None,
) -> models.Incident:
    """Registra una falta. Si la misma persona tiene una alerta pendiente reciente, la falta
    se añade a esa alerta (con su captura); si no, se crea una alerta nueva (created_by = None).
    Si la captura falla, la falta se registra igual: lo importante es avisar.
    """
    now = datetime.now(timezone.utc)
    window = GROUP_SECONDS if group_seconds is None else group_seconds
    from app.modules.reglas_lab.sesiones import open_session_for
    session = open_session_for(db, workshop_id)  # práctica en curso del laboratorio
    session_id = session.id if session else None
    incident = _open_alert_for(db, camera_id, event, now, window, session_id)
    equipment = db.query(models.SafetyEquipment).filter_by(id=event.equipment_id).first()
    if incident is not None and any(f.equipment_id == event.equipment_id and f.detail == event.detail
                                    for f in incident_faults(db, incident)):
        return incident  # esa falta ya está en la alerta (una bata abierta que pasa a ausente sí se añade)

    if incident is None:
        camera = db.query(Camera).filter_by(id=camera_id).first()
        workshop = db.query(models.Workshop).filter_by(id=workshop_id).first()
        incident = models.Incident(
            code=new_incident_code(now),
            workshop_id=workshop_id,
            camera_id=camera_id,
            camera_name=camera.name if camera else None,
            workshop_name=workshop.name if workshop else None,
            laboratory_type=workshop.laboratory_type if workshop else None,
            equipment_id=equipment.id if equipment else None,
            title='-', description='-',
            severity=event.severity,
            status='PENDIENTE',
            detected_class=event.model_class,
            confidence=round(1.0 - event.present_ratio, 3),
            bounding_box=[round(v, 1) for v in event.person_box],
            frame_width=frame_size[0] if frame_size else None,
            frame_height=frame_size[1] if frame_size else None,
            occurred_at=now,
            created_by=None,
            rule_code=event.rule_code,
            track_id=event.track_id,
            session_id=session_id,
        )
        db.add(incident)
        db.flush()

    fault = models.IncidentFault(
        incident_id=incident.id,
        equipment_id=equipment.id if equipment else None,
        rule_code=event.rule_code,
        severity=event.severity,
        present_ratio=round(event.present_ratio, 3),
        detail=event.detail,
        detected_at=now,
    )
    db.add(fault)
    db.flush()
    if save_capture:
        try:
            fault.capture_path = save_capture(f'{incident.code}-{fault.id}')
        except OSError:
            fault.capture_path = None
    if incident.capture_path is None:
        incident.capture_path = fault.capture_path  # primera evidencia (compatibilidad)
    _refresh_summary(db, incident)
    db.commit()
    db.refresh(incident)
    events.notify('incidents')  # las alertas aparecen al instante en todas las pantallas
    return incident


# Nombre anterior, usado por el monitoreo.
create_system_incident = record_system_fault


def escalation_seconds() -> float:
    """Tras este tiempo sin respuesta, una alerta pendiente se avisa al administrador.
    Nunca se confirma sola. SAFEVISION_ESCALAR_MIN (minutos, por defecto 5)."""
    import os
    try:
        return max(0.0, float(os.environ.get('SAFEVISION_ESCALAR_MIN', '5')) * 60)
    except ValueError:
        return 300.0


def active_camera(db: Session, camera_id: int) -> Camera | None:
    return db.query(Camera).filter(Camera.id == camera_id, Camera.is_active.is_(True)).first()
