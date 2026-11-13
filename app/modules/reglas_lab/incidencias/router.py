"""Rutas de incidencias (alertas): historial con filtros, registro manual, validación por el encargado
(confirmar con la acción tomada o descartar) y capturas de evidencia.

Las URL no cambian: todas cuelgan de /modules/reglas_lab (ver reglas_lab/router.py)."""
from datetime import date, datetime, time, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.bitacora import registrar
from app.core.permisos import allowed_workshop_ids
from app.modules.ai_vision.models import Camera
from app.modules.auth_db.dependencies import get_current_user
from app.modules.auth_db.models import User
from app.modules.reglas_lab import models
from app.modules.reglas_lab.comun import as_utc, ensure_workshop_access, get_db, naive_utc
from app.modules.reglas_lab.incidencias import schemas

router = APIRouter()


def _faults_by_incident(db: Session, incidents: list) -> dict[int, list[dict]]:
    """Faltas (evidencias) de cada alerta. Las incidencias antiguas sin faltas se
    muestran como una sola falta con los datos de la incidencia."""
    ids = [incident.id for incident in incidents]
    rows = (db.query(models.IncidentFault).filter(models.IncidentFault.incident_id.in_(ids))
            .order_by(models.IncidentFault.detected_at, models.IncidentFault.id).all()) if ids else []
    equipment_ids = {row.equipment_id for row in rows} | {i.equipment_id for i in incidents}
    names = dict(db.query(models.SafetyEquipment.id, models.SafetyEquipment.name)
                 .filter(models.SafetyEquipment.id.in_([e for e in equipment_ids if e])).all())
    result: dict[int, list[dict]] = {incident.id: [] for incident in incidents}
    for row in rows:
        result[row.incident_id].append({
            'id': row.id, 'equipment_id': row.equipment_id, 'equipment_name': names.get(row.equipment_id),
            'rule_code': row.rule_code, 'severity': row.severity, 'has_capture': bool(row.capture_path),
            'detected_at': as_utc(row.detected_at), 'detail': row.detail,
        })
    for incident in incidents:
        if not result[incident.id]:
            result[incident.id].append({
                'id': None, 'equipment_id': incident.equipment_id, 'equipment_name': names.get(incident.equipment_id),
                'rule_code': incident.rule_code, 'severity': incident.severity,
                'has_capture': bool(incident.capture_path), 'detected_at': as_utc(incident.occurred_at),
            })
    return result


def _session_codes(db: Session, incidents: list) -> dict[int, str]:
    ids = {incident.session_id for incident in incidents if incident.session_id}
    if not ids:
        return {}
    return dict(db.query(models.PracticeSession.id, models.PracticeSession.code)
                .filter(models.PracticeSession.id.in_(ids)).all())


def _incident_response(incident, workshop_name: str, camera_name: str, equipment_name: str | None,
                       reviewer_name: str | None = None, faults: list[dict] | None = None,
                       session_code: str | None = None) -> dict:
    return {
        'session_id': incident.session_id,
        'session_code': session_code,
        'id': incident.id,
        'code': incident.code,
        'workshop_id': incident.workshop_id,
        'workshop_name': incident.workshop_name or workshop_name or f'Taller {incident.workshop_id}',
        'camera_id': incident.camera_id,
        'camera_name': incident.camera_name or camera_name or f'Cámara {incident.camera_id}',
        'equipment_id': incident.equipment_id,
        'equipment_name': equipment_name,
        'title': incident.title,
        'description': incident.description,
        'severity': incident.severity,
        'status': incident.status,
        'detected_class': incident.detected_class,
        'confidence': incident.confidence,
        'bounding_box': incident.bounding_box,
        'frame_width': incident.frame_width,
        'frame_height': incident.frame_height,
        'occurred_at': as_utc(incident.occurred_at),
        'rule_code': incident.rule_code,
        'has_capture': bool(incident.capture_path),
        'created_by_system': incident.created_by is None,
        'reviewed_by_name': reviewer_name,
        'reviewed_at': as_utc(incident.reviewed_at),
        'review_reason': incident.review_reason,
        'action_taken': incident.action_taken,
        'faults': faults or [],
        **_escalation(incident),
    }


def _escalation(incident) -> dict:
    from app.modules.reglas_lab.incidencias.servicio import escalation_seconds
    if incident.status not in ('PENDIENTE', 'EN_REVISION') or incident.occurred_at is None:
        return {'pending_seconds': None, 'escalated': False}
    waited = (datetime.now(timezone.utc) - as_utc(incident.occurred_at)).total_seconds()
    return {'pending_seconds': int(waited), 'escalated': waited >= escalation_seconds()}


@router.get('/incidents', response_model=list[schemas.IncidentResponse])
def list_incidents(
    workshop_id: int | None = None,
    camera_id: int | None = None,
    session_id: int | None = None,
    active_session: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    incident_status: schemas.IncidentStatus | None = Query(None, alias='status'),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Incidencias visibles para el usuario, las más recientes primero (como máximo `limit`).

    Periodo: `start`/`end` con zona horaria (la web envía el inicio y el fin del día en su hora local);
    `date_from`/`date_to` son días en UTC y se mantienen por compatibilidad.
    """
    query = (
        db.query(models.Incident, models.Workshop.name, Camera.name, models.SafetyEquipment.name, User.full_name)
        .outerjoin(models.Workshop, models.Workshop.id == models.Incident.workshop_id)
        .outerjoin(Camera, Camera.id == models.Incident.camera_id)  # la cámara puede haberse eliminado
        .outerjoin(models.SafetyEquipment, models.SafetyEquipment.id == models.Incident.equipment_id)
        .outerjoin(User, User.id == models.Incident.reviewed_by)
    )
    allowed = allowed_workshop_ids(current_user)
    if allowed is not None:  # encargado: incidencias de su laboratorio
        query = query.filter(models.Incident.workshop_id.in_(allowed))
    if workshop_id is not None:
        query = query.filter(models.Incident.workshop_id == workshop_id)
    if camera_id is not None:
        query = query.filter(models.Incident.camera_id == camera_id)
    if session_id is not None:
        query = query.filter(models.Incident.session_id == session_id)
    if active_session:
        # Panel de alertas: solo las de prácticas en curso. Las de prácticas terminadas siguen en el
        # historial con su sesión, pero ya no son alertas que atender en la sala.
        open_sessions = db.query(models.PracticeSession.id).filter(models.PracticeSession.ended_at.is_(None))
        query = query.filter(models.Incident.session_id.in_(open_sessions))
    if date_from:
        query = query.filter(models.Incident.occurred_at >= datetime.combine(date_from, time.min, timezone.utc))
    if date_to:
        query = query.filter(models.Incident.occurred_at < datetime.combine(date_to, time.max, timezone.utc))
    if start is not None:
        query = query.filter(models.Incident.occurred_at >= naive_utc(start))
    if end is not None:
        query = query.filter(models.Incident.occurred_at < naive_utc(end))
    if incident_status:
        query = query.filter(models.Incident.status == incident_status)

    rows = query.order_by(models.Incident.occurred_at.desc()).limit(limit).all()
    faults = _faults_by_incident(db, [row[0] for row in rows])
    codes = _session_codes(db, [row[0] for row in rows])
    return [
        _incident_response(incident, workshop_name, camera_name, equipment_name, reviewer_name, faults[incident.id],
                           codes.get(incident.session_id))
        for incident, workshop_name, camera_name, equipment_name, reviewer_name in rows
    ]


@router.post('/incidents', response_model=schemas.IncidentResponse, status_code=status.HTTP_201_CREATED)
def create_incident(
    payload: schemas.IncidentCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    workshop = db.query(models.Workshop).filter_by(id=payload.workshop_id, is_active=True).first()
    if not workshop:
        raise HTTPException(status_code=404, detail='Taller no encontrado')
    ensure_workshop_access(workshop, current_user)
    camera_assignment = db.query(models.WorkshopCamera).filter_by(
        workshop_id=workshop.id,
        camera_id=payload.camera_id,
    ).first()
    if not camera_assignment:
        raise HTTPException(status_code=400, detail='La cámara no está asignada al taller indicado')
    if payload.equipment_id and not db.query(models.SafetyEquipment).filter_by(id=payload.equipment_id, is_active=True).first():
        raise HTTPException(status_code=400, detail='Equipo de seguridad desconocido')

    from app.modules.reglas_lab.practicas.sesiones import open_session_for
    incident = models.Incident(
        code=f'INC-{datetime.now(timezone.utc):%Y%m%d}-{uuid4().hex[:8].upper()}',
        workshop_id=workshop.id,
        camera_id=payload.camera_id,
        camera_name=db.query(Camera.name).filter_by(id=payload.camera_id).scalar(),
        workshop_name=workshop.name,
        laboratory_type=workshop.laboratory_type,
        equipment_id=payload.equipment_id,
        title=payload.title.strip(),
        description=payload.description.strip(),
        severity=payload.severity,
        detected_class=payload.detected_class,
        confidence=payload.confidence,
        bounding_box=payload.bounding_box,
        frame_width=payload.frame_width,
        frame_height=payload.frame_height,
        occurred_at=payload.occurred_at or datetime.now(timezone.utc),
        created_by=current_user.id,
        session_id=getattr(open_session_for(db, workshop.id), 'id', None),  # práctica en curso, si hay
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    registrar(db, current_user, 'CREAR_INCIDENCIA_MANUAL', 'incidencias',
              f'{incident.code} en {workshop.name}: {incident.title}')
    equipment = db.query(models.SafetyEquipment).filter_by(id=incident.equipment_id).first() if incident.equipment_id else None
    return {
        'id': incident.id,
        'code': incident.code,
        'workshop_id': workshop.id,
        'workshop_name': workshop.name,
        'camera_id': payload.camera_id,
        'camera_name': incident.camera_name,
        'equipment_id': incident.equipment_id,
        'equipment_name': equipment.name if equipment else None,
        'title': incident.title,
        'description': incident.description,
        'severity': incident.severity,
        'status': incident.status,
        'detected_class': incident.detected_class,
        'confidence': incident.confidence,
        'bounding_box': incident.bounding_box,
        'frame_width': incident.frame_width,
        'frame_height': incident.frame_height,
        'occurred_at': as_utc(incident.occurred_at),
        'session_id': incident.session_id,
        'session_code': _session_codes(db, [incident]).get(incident.session_id),
    }


# Una alerta solo cambia de estado al validarla (POST /incidents/{id}/review), que guarda quién, cuándo y
# la acción tomada. Ya no existe una ruta para cambiar el estado sin esos datos.
REVIEWED_STATUSES = {'CONFIRMADA', 'DESCARTADA'}


def _single_incident_response(db: Session, incident, workshop) -> dict:
    camera_name = db.query(Camera.name).filter_by(id=incident.camera_id).scalar()
    equipment_name = db.query(models.SafetyEquipment.name).filter_by(id=incident.equipment_id).scalar()
    reviewer_name = db.query(User.full_name).filter_by(id=incident.reviewed_by).scalar() if incident.reviewed_by else None
    faults = _faults_by_incident(db, [incident])[incident.id]
    return _incident_response(incident, workshop.name, camera_name, equipment_name, reviewer_name, faults,
                              _session_codes(db, [incident]).get(incident.session_id))


@router.post('/incidents/{incident_id}/review', response_model=schemas.IncidentResponse)
def review_incident(
    incident_id: int,
    payload: schemas.IncidentReview,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Confirma o descarta una alerta. Solo el encargado del área o el administrador."""
    from app.modules.ai_vision.capture import delete_capture

    incident = db.query(models.Incident).filter_by(id=incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail='Incidencia no encontrada')
    workshop = db.query(models.Workshop).filter_by(id=incident.workshop_id).first()
    ensure_workshop_access(workshop, current_user)
    if incident.status in REVIEWED_STATUSES:
        raise HTTPException(status_code=409, detail='La incidencia ya fue validada')
    if payload.decision == 'CONFIRMADA' and not payload.action:
        raise HTTPException(status_code=422, detail='Indica la acción tomada: CORREGIDO o RETIRADO_DE_PRACTICA')

    incident.status = payload.decision
    incident.reviewed_by = current_user.id
    incident.reviewed_at = datetime.now(timezone.utc)
    incident.review_reason = (payload.reason or '').strip() or None
    incident.action_taken = payload.action if payload.decision == 'CONFIRMADA' else None
    if payload.decision == 'DESCARTADA':
        # Una falsa alarma no conserva imágenes; quedan solo los datos mínimos para medirla.
        for fault in db.query(models.IncidentFault).filter_by(incident_id=incident.id).all():
            if fault.capture_path:
                delete_capture(fault.capture_path)
                fault.capture_path = None
        if incident.capture_path:
            delete_capture(incident.capture_path)
            incident.capture_path = None
    db.commit()
    if payload.decision == 'CONFIRMADA':
        registrar(db, current_user, 'CONFIRMAR_ALERTA', 'incidencias',
                  f'{incident.code} ({incident.title}): {payload.action}')
    else:
        registrar(db, current_user, 'DESCARTAR_ALERTA', 'incidencias',
                  f'{incident.code} ({incident.title})' + (f': {incident.review_reason}' if incident.review_reason else ''))
    return _single_incident_response(db, incident, workshop)


def _serve_capture(db: Session, incident_id: int, current_user, fault_id: int | None = None):
    """Devuelve la captura enmascarada tras comprobar el acceso al taller de la incidencia.

    La ruta del archivo sale siempre de la base de datos, nunca de la petición.
    """
    from fastapi.responses import FileResponse
    from app.modules.ai_vision.capture import CAPTURE_DIR, PROJECT_ROOT

    incident = db.query(models.Incident).filter_by(id=incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail='Incidencia no encontrada')
    workshop = db.query(models.Workshop).filter_by(id=incident.workshop_id).first()
    ensure_workshop_access(workshop, current_user)
    if fault_id is None:
        capture_path = incident.capture_path
    else:
        fault = db.query(models.IncidentFault).filter_by(id=fault_id, incident_id=incident.id).first()
        if not fault:
            raise HTTPException(status_code=404, detail='Falta no encontrada')
        capture_path = fault.capture_path
    if not capture_path:
        raise HTTPException(status_code=404, detail='La incidencia no tiene captura')
    path = (PROJECT_ROOT / capture_path).resolve()
    try:
        path.relative_to(CAPTURE_DIR.resolve())  # nunca servir archivos fuera de capturas/
    except ValueError as error:
        raise HTTPException(status_code=404, detail='Captura no disponible') from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail='Captura no disponible')
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'private, no-store'})


@router.get('/incidents/{incident_id}/faults/{fault_id}/capture')
def fault_capture(incident_id: int, fault_id: int, db: Session = Depends(get_db),
                  current_user=Depends(get_current_user)):
    """Evidencia de una falta concreta de la alerta."""
    return _serve_capture(db, incident_id, current_user, fault_id)


@router.get('/incidents/{incident_id}/capture')
def incident_capture(incident_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Primera captura de la incidencia, solo para quien puede ver ese taller."""
    return _serve_capture(db, incident_id, current_user)
