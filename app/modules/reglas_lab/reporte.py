"""Reporte SST de un periodo: resumen, sesiones de práctica, laboratorios, EPP y bitácora, todo calculado con
lo guardado en la base (sesiones e incidencias).

No hay datos de ejemplo: si en el periodo no hubo alertas, el reporte sale con ceros.
La huella SHA-256 se calcula con el contenido del reporte (sin la hora de emisión): si alguien cambia
una incidencia del periodo y se vuelve a generar, la huella cambia.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.ai_vision.models import Camera
from app.modules.auth_db.models import User
from app.modules.reglas_lab import models

SEVERITIES = ('CRÍTICO', 'ALTO', 'MEDIO')
CONFIRMED = {'CONFIRMADA', 'ATENDIDO'}  # ATENDIDO: estado manual de datos anteriores
DISCARDED = {'DESCARTADA'}
PENDING = {'PENDIENTE', 'EN_REVISION'}
MAX_ROWS = 300  # filas de la bitácora; el resumen siempre cuenta todas


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _naive_utc(value: datetime) -> datetime:
    """SQLite guarda las fechas en UTC sin zona; los filtros se comparan igual."""
    return _utc(value).replace(tzinfo=None)


def _average(values: list[float]) -> int | None:
    return round(sum(values) / len(values)) if values else None


def _counts(statuses: list[str]) -> dict:
    return {
        'alerts': len(statuses),
        'confirmed': sum(s in CONFIRMED for s in statuses),
        'discarded': sum(s in DISCARDED for s in statuses),
        'pending': sum(s in PENDING for s in statuses),
    }


def build_report(db: Session, start: datetime, end: datetime, workshop_ids: set[int] | None) -> dict:
    """`workshop_ids` None = todos los laboratorios; un conjunto vacío = ninguno."""
    query = db.query(models.Incident).filter(
        models.Incident.occurred_at >= _naive_utc(start),
        models.Incident.occurred_at < _naive_utc(end),
    )
    if workshop_ids is not None:
        query = query.filter(models.Incident.workshop_id.in_(workshop_ids or {-1}))
    incidents = query.order_by(models.Incident.occurred_at, models.Incident.id).all()
    ids = [i.id for i in incidents]

    faults_by_incident: dict[int, list] = defaultdict(list)
    if ids:
        for fault in (db.query(models.IncidentFault).filter(models.IncidentFault.incident_id.in_(ids))
                      .order_by(models.IncidentFault.detected_at, models.IncidentFault.id)):
            faults_by_incident[fault.incident_id].append(fault)

    equipment_names = dict(db.query(models.SafetyEquipment.id, models.SafetyEquipment.name).all())
    camera_names = dict(db.query(Camera.id, Camera.name).all())
    reviewer_ids = {i.reviewed_by for i in incidents if i.reviewed_by}
    reviewers = dict(db.query(User.id, User.full_name).filter(User.id.in_(reviewer_ids)).all()) if reviewer_ids else {}

    # Laboratorios: los del alcance (aunque no tengan alertas) y los que aparecen en las alertas.
    workshop_query = db.query(models.Workshop)
    if workshop_ids is not None:
        workshop_query = workshop_query.filter(models.Workshop.id.in_(workshop_ids or {-1}))
    workshops = {w.id: w for w in workshop_query.all()
                 if w.is_active or any(i.workshop_id == w.id for i in incidents)}
    campus_names = dict(db.query(models.Campus.id, models.Campus.name).all())
    required: dict[int, list[str]] = defaultdict(list)
    for rule in (db.query(models.WorkshopEquipmentRule)
                 .filter(models.WorkshopEquipmentRule.workshop_id.in_(workshops or {-1}),
                         models.WorkshopEquipmentRule.is_required.is_(True))
                 .order_by(models.WorkshopEquipmentRule.equipment_id)):
        required[rule.workshop_id].append(equipment_names.get(rule.equipment_id, rule.equipment_id))

    # Sesiones de práctica que se solapan con el periodo; solo cuenta el tiempo dentro del periodo.
    session_query = db.query(models.PracticeSession).filter(
        models.PracticeSession.started_at < _naive_utc(end),
        models.PracticeSession.ended_at.is_(None) | (models.PracticeSession.ended_at > _naive_utc(start)),
    )
    if workshop_ids is not None:
        session_query = session_query.filter(models.PracticeSession.workshop_id.in_(workshop_ids or {-1}))
    sessions = session_query.order_by(models.PracticeSession.started_at).all()
    now = datetime.now(timezone.utc)
    alerts_by_session = Counter(i.session_id for i in incidents if i.session_id)
    session_rows = []
    supervised: Counter = Counter()
    for session in sessions:
        s_start, s_end = _utc(session.started_at), _utc(session.ended_at)
        seconds = max(0.0, (min(s_end or now, _utc(end)) - max(s_start, _utc(start))).total_seconds())
        supervised[session.workshop_id] += seconds
        session_rows.append({
            'id': session.id,
            'code': session.code,
            'workshop_id': session.workshop_id,
            'workshop_name': session.workshop_name or f'Laboratorio {session.workshop_id}',
            'started_at': s_start,
            'ended_at': s_end,
            'end_reason': session.end_reason,
            'supervised_seconds': round(seconds),
            'alerts': alerts_by_session.get(session.id, 0),
        })
    session_codes = {row['id']: row['code'] for row in session_rows}
    session_counts = Counter(session.workshop_id for session in sessions)

    rows, response_times = [], []
    severity_counts: Counter = Counter()
    minor_faults = 0
    equipment_faults: Counter = Counter()
    equipment_confirmed: Counter = Counter()
    actions: Counter = Counter()
    per_workshop: dict[int, dict] = defaultdict(lambda: {'statuses': [], 'faults': 0, 'times': []})

    for incident in incidents:
        faults = faults_by_incident.get(incident.id) or [None]  # incidencias antiguas: una falta implícita
        fault_rows = []
        for fault in faults:
            equipment_id = fault.equipment_id if fault else incident.equipment_id
            severity = fault.severity if fault else incident.severity
            detail = fault.detail if fault else None
            severity_counts[severity] += 1
            minor_faults += detail is not None
            key = equipment_id or 'otro'
            equipment_faults[key] += 1
            if incident.status in CONFIRMED:
                equipment_confirmed[key] += 1
            fault_rows.append({
                'equipment_name': equipment_names.get(equipment_id) if equipment_id else None,
                'rule_code': fault.rule_code if fault else incident.rule_code,
                'severity': severity,
                'detail': detail,
            })

        occurred, reviewed = _utc(incident.occurred_at), _utc(incident.reviewed_at)
        response = (reviewed - occurred).total_seconds() if reviewed and occurred else None
        if response is not None and incident.status in CONFIRMED | DISCARDED:
            response_times.append(response)
        if incident.action_taken:
            actions[incident.action_taken] += 1

        stats = per_workshop[incident.workshop_id]
        stats['statuses'].append(incident.status)
        stats['faults'] += len(faults)
        if response is not None:
            stats['times'].append(response)

        rows.append({
            'id': incident.id,
            'code': incident.code,
            'occurred_at': occurred,
            'workshop_id': incident.workshop_id,
            'workshop_name': incident.workshop_name or getattr(workshops.get(incident.workshop_id), 'name', None)
            or f'Laboratorio {incident.workshop_id}',
            'camera_name': incident.camera_name or camera_names.get(incident.camera_id) or f'Cámara {incident.camera_id}',
            'severity': incident.severity,
            'status': incident.status,
            'action_taken': incident.action_taken,
            'reviewed_by_name': reviewers.get(incident.reviewed_by),
            'response_seconds': round(response) if response is not None else None,
            'session_id': incident.session_id,
            'session_code': session_codes.get(incident.session_id),
            'faults': fault_rows,
        })

    statuses = [i.status for i in incidents]
    totals = _counts(statuses)
    reviewed_total = totals['confirmed'] + totals['discarded']
    summary = {
        **totals,
        'faults': sum(severity_counts.values()),
        'minor_faults': minor_faults,
        'faults_by_severity': {s: severity_counts.get(s, 0) for s in SEVERITIES},
        'precision': round(totals['confirmed'] / reviewed_total, 4) if reviewed_total else None,
        'avg_response_seconds': _average(response_times),
        'sessions': len(sessions),
        'supervised_seconds': round(sum(supervised.values())),
        'alerts_outside_sessions': sum(1 for i in incidents if not i.session_id),
        'actions': {'CORREGIDO': actions.get('CORREGIDO', 0),
                    'RETIRADO_DE_PRACTICA': actions.get('RETIRADO_DE_PRACTICA', 0)},
    }

    workshop_rows = []
    for workshop in sorted(workshops.values(), key=lambda w: (campus_names.get(w.campus_id) or '', w.name)):
        stats = per_workshop.get(workshop.id, {'statuses': [], 'faults': 0, 'times': []})
        workshop_rows.append({
            'workshop_id': workshop.id,
            'name': workshop.name,
            'laboratory_type': workshop.laboratory_type,
            'campus_name': campus_names.get(workshop.campus_id),
            'required_equipment': required.get(workshop.id, []),
            **_counts(stats['statuses']),
            'faults': stats['faults'],
            'avg_response_seconds': _average(stats['times']),
            'sessions': session_counts.get(workshop.id, 0),
            'supervised_seconds': round(supervised.get(workshop.id, 0)),
        })

    equipment_rows = sorted(
        ({'equipment_id': key, 'name': equipment_names.get(key, 'Sin EPP indicado'),
          'faults': count, 'confirmed_faults': equipment_confirmed.get(key, 0)}
         for key, count in equipment_faults.items()),
        key=lambda row: (-row['faults'], row['name']),
    )

    body = {
        'start': _utc(start),
        'end': _utc(end),
        'summary': summary,
        'workshops': workshop_rows,
        'equipment': equipment_rows,
        'sessions': session_rows[::-1],  # las más recientes primero
        'incidents': rows[-MAX_ROWS:][::-1],  # las más recientes primero
        'incidents_truncated': len(rows) > MAX_ROWS,
    }
    fingerprint = hashlib.sha256(
        json.dumps({**body, 'incidents': rows}, default=str, sort_keys=True, ensure_ascii=False).encode('utf-8')
    ).hexdigest()
    generated = datetime.now(timezone.utc)
    return {
        'report_id': f'RPT-{generated:%Y%m%d-%H%M%S}',
        'generated_at': generated,
        'fingerprint': fingerprint,
        **body,
    }
