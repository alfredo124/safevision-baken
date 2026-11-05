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

from app.modules.ai_vision.models import Camera
from app.modules.reglas_lab import models
from app.modules.reglas_lab.compliance import EquipmentRule, ViolationEvent


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
    incident.title = (f'{labels[0]}: no se detecta' if len(labels) == 1
                      else f'Faltan {len(labels)} EPP: {", ".join(labels)}')[:160]
    incident.severity = max((f.severity for f in faults), key=lambda value: SEVERITY_ORDER.get(value, 0))
    incident.description = (
        f'Reglas {codes}: el sistema no detectó {", ".join(label.lower() for label in labels)} '
        f'en zonas visibles de la misma persona. Pendiente de validación del encargado.'
    )[:1000]


def _open_alert_for(db: Session, camera_id: int, event: ViolationEvent, now: datetime,
                    group_seconds: float) -> models.Incident | None:
    """Alerta pendiente de la misma persona con actividad reciente, si existe."""
    candidates = (
        db.query(models.Incident)
        .filter(models.Incident.camera_id == camera_id, models.Incident.status == 'PENDIENTE',
                models.Incident.created_by.is_(None))
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
    incident = _open_alert_for(db, camera_id, event, now, window)
    equipment = db.query(models.SafetyEquipment).filter_by(id=event.equipment_id).first()
    if incident is not None and any(f.equipment_id == event.equipment_id for f in incident_faults(db, incident)):
        return incident  # esa falta ya está en la alerta

    if incident is None:
        incident = models.Incident(
            code=new_incident_code(now),
            workshop_id=workshop_id,
            camera_id=camera_id,
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
        )
        db.add(incident)
        db.flush()

    fault = models.IncidentFault(
        incident_id=incident.id,
        equipment_id=equipment.id if equipment else None,
        rule_code=event.rule_code,
        severity=event.severity,
        present_ratio=round(event.present_ratio, 3),
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


def test_mode_enabled() -> bool:
    """Fase de prueba: permite borrar incidencias de prueba. SAFEVISION_MODO_PRUEBA=0 lo desactiva."""
    import os
    return os.environ.get('SAFEVISION_MODO_PRUEBA', '1').strip().lower() not in ('0', 'false', 'no')


def clear_workshop_incidents(db: Session, workshop_id: int) -> int:
    """Borra las incidencias de un taller con sus faltas y capturas. Solo para la fase de prueba."""
    from app.modules.ai_vision.capture import delete_capture

    incidents = db.query(models.Incident).filter_by(workshop_id=workshop_id).all()
    ids = [incident.id for incident in incidents]
    if not ids:
        return 0
    faults = db.query(models.IncidentFault).filter(models.IncidentFault.incident_id.in_(ids)).all()
    for path in {f.capture_path for f in faults} | {i.capture_path for i in incidents}:
        if path:
            delete_capture(path)
    db.query(models.IncidentFault).filter(models.IncidentFault.incident_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Incident).filter(models.Incident.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return len(ids)


def active_camera(db: Session, camera_id: int) -> Camera | None:
    return db.query(Camera).filter(Camera.id == camera_id, Camera.is_active.is_(True)).first()
