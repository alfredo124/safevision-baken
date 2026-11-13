"""Sesiones de práctica: lo ocurrido en cada laboratorio queda guardado por práctica.

- "Iniciar práctica" abre una sesión (si ya hay una abierta en ese laboratorio, se sigue usando).
- Cada incidencia guarda la sesión abierta de su laboratorio (incident_service.record_system_fault).
- "Finalizar práctica" la cierra con la hora y el usuario.
- Cada sesión guarda una copia de las reglas de EPP exigidas (rules_snapshot), y una versión nueva si se
  cambian con la práctica en curso. Así el reporte de un periodo pasado muestra lo que se exigía entonces.
- Al arrancar el servidor, las sesiones que quedaron abiertas se cierran como INTERRUMPIDA: el análisis
  vivía en memoria y ya no corre. Su fin es la última actividad conocida (la última falta registrada).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modules.reglas_lab import models

FINALIZADA = 'FINALIZADA'
INTERRUMPIDA = 'INTERRUMPIDA'


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def open_session_for(db: Session, workshop_id: int) -> models.PracticeSession | None:
    return (db.query(models.PracticeSession)
            .filter(models.PracticeSession.workshop_id == workshop_id, models.PracticeSession.ended_at.is_(None))
            .order_by(models.PracticeSession.id.desc()).first())


def current_rules(db: Session, workshop_id: int) -> list[dict]:
    """Reglas de EPP vigentes del laboratorio, con el nombre del equipo y el código y la gravedad efectivos
    (los del reglamento o, si faltan, los del catálogo). Es lo que se copia en cada práctica."""
    rows = (
        db.query(models.WorkshopEquipmentRule, models.SafetyEquipment)
        .join(models.SafetyEquipment, models.SafetyEquipment.id == models.WorkshopEquipmentRule.equipment_id)
        .filter(models.WorkshopEquipmentRule.workshop_id == workshop_id,
                models.WorkshopEquipmentRule.is_required.is_(True))
        .order_by(models.WorkshopEquipmentRule.equipment_id)
        .all()
    )
    return [{
        'equipment_id': equipment.id,
        'name': equipment.name,
        'rule_code': rule.rule_code or equipment.missing_code,
        'severity': rule.severity or equipment.impact,
        'manual_verification': not equipment.model_class,  # sin detector: la revisa el encargado
    } for rule, equipment in rows]


def record_rules(db: Session, session: models.PracticeSession, now: datetime | None = None) -> bool:
    """Añade a la sesión la versión vigente de las reglas si es distinta de la última guardada.
    No hace commit. Devuelve True si añadió una versión."""
    rules = current_rules(db, session.workshop_id)
    versions = list(session.rules_snapshot or [])
    if versions and versions[-1].get('rules') == rules:
        return False
    versions.append({'since': (now or datetime.now(timezone.utc)).isoformat(), 'rules': rules})
    session.rules_snapshot = versions  # se reasigna la lista: SQLAlchemy no detecta cambios dentro del JSON
    return True


def start_session(db: Session, workshop: models.Workshop, user_id: int | None) -> models.PracticeSession:
    """Abre la sesión del laboratorio o devuelve la que ya está abierta."""
    session = open_session_for(db, workshop.id)
    if session is not None:
        return session
    now = datetime.now(timezone.utc)
    campus = db.get(models.Campus, workshop.campus_id) if workshop.campus_id else None
    session = models.PracticeSession(
        code=f'SES-{now:%Y%m%d}-{uuid4().hex[:6].upper()}',
        workshop_id=workshop.id,
        workshop_name=workshop.name,
        campus_name=campus.name if campus else None,
        laboratory_type=workshop.laboratory_type,
        started_at=now,
        started_by=user_id,
    )
    record_rules(db, session, now)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def end_session(db: Session, workshop_id: int, user_id: int | None) -> models.PracticeSession | None:
    session = open_session_for(db, workshop_id)
    if session is None:
        return None
    session.ended_at = datetime.now(timezone.utc)
    session.ended_by = user_id
    session.end_reason = FINALIZADA
    db.commit()
    db.refresh(session)
    return session


def _last_activity(db: Session, session: models.PracticeSession) -> datetime:
    last_incident = (db.query(func.max(models.Incident.occurred_at))
                     .filter(models.Incident.session_id == session.id).scalar())
    last_fault = (db.query(func.max(models.IncidentFault.detected_at))
                  .join(models.Incident, models.Incident.id == models.IncidentFault.incident_id)
                  .filter(models.Incident.session_id == session.id).scalar())
    candidates = [_utc(value) for value in (session.started_at, last_incident, last_fault) if value is not None]
    return max(candidates)


def close_interrupted_sessions(db: Session) -> int:
    """Cierra las sesiones abiertas al arrancar el servidor (ninguna práctica sigue corriendo)."""
    pending = db.query(models.PracticeSession).filter(models.PracticeSession.ended_at.is_(None)).all()
    for session in pending:
        session.ended_at = _last_activity(db, session)
        session.end_reason = INTERRUMPIDA
    if pending:
        db.commit()
    return len(pending)


def session_stats(db: Session, session_ids: list[int]) -> dict[int, dict]:
    """Alertas de cada sesión por estado."""
    from app.modules.reglas_lab.reporte import CONFIRMED, DISCARDED, PENDING
    stats = {sid: {'alerts': 0, 'confirmed': 0, 'discarded': 0, 'pending': 0} for sid in session_ids}
    if not session_ids:
        return stats
    rows = (db.query(models.Incident.session_id, models.Incident.status, func.count())
            .filter(models.Incident.session_id.in_(session_ids))
            .group_by(models.Incident.session_id, models.Incident.status).all())
    for session_id, status, count in rows:
        item = stats[session_id]
        item['alerts'] += count
        if status in CONFIRMED:
            item['confirmed'] += count
        elif status in DISCARDED:
            item['discarded'] += count
        elif status in PENDING:
            item['pending'] += count
    return stats


def session_response(session: models.PracticeSession, stats: dict | None = None,
                     user_names: dict[int, str] | None = None) -> dict:
    names = user_names or {}
    started, ended = _utc(session.started_at), _utc(session.ended_at)
    end_for_duration = ended or datetime.now(timezone.utc)
    return {
        'id': session.id,
        'code': session.code,
        'workshop_id': session.workshop_id,
        'workshop_name': session.workshop_name or f'Laboratorio {session.workshop_id}',
        'campus_name': session.campus_name,
        'laboratory_type': session.laboratory_type,
        'started_at': started,
        'ended_at': ended,
        'active': ended is None,
        'end_reason': session.end_reason,
        'duration_seconds': max(0, round((end_for_duration - started).total_seconds())),
        'started_by_name': names.get(session.started_by),
        'ended_by_name': names.get(session.ended_by),
        **(stats or {'alerts': 0, 'confirmed': 0, 'discarded': 0, 'pending': 0}),
    }


def user_names(db: Session, sessions: list[models.PracticeSession]) -> dict[int, str]:
    from app.modules.auth_db.models import User
    ids = {s.started_by for s in sessions if s.started_by} | {s.ended_by for s in sessions if s.ended_by}
    if not ids:
        return {}
    return {row.id: row.full_name or row.username for row in db.query(User).filter(User.id.in_(ids)).all()}
