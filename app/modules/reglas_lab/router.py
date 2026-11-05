from datetime import date, datetime, time, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.modules.ai_vision.models import Camera
from app.modules.auth_db.dependencies import get_current_user
from app.modules.auth_db.models import User
from app.modules.reglas_lab import models, schemas

router = APIRouter(prefix='/modules/reglas_lab', tags=['reglas_lab'])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _role(current_user) -> str:
    return current_user.role.value


def _ensure_workshop_access(workshop: models.Workshop, current_user) -> None:
    role = _role(current_user)
    permitted_area = {'ENCARGADO_QUIMICA': 'QUIMICA', 'ENCARGADO_FISICA': 'FISICA'}.get(role)
    if role != 'ADMINISTRADOR' and permitted_area != workshop.laboratory_type:
        raise HTTPException(status_code=403, detail='No tienes acceso a este taller')


def _ensure_can_manage(current_user) -> None:
    if _role(current_user) not in {'ADMINISTRADOR', 'ENCARGADO_QUIMICA', 'ENCARGADO_FISICA'}:
        raise HTTPException(status_code=403, detail='No tienes permiso para modificar reglas del laboratorio')


def _camera_summary(camera: Camera) -> dict:
    return {
        'id': camera.id,
        'name': camera.name,
        'laboratory_type': camera.laboratory_type,
        'status': camera.status or 'offline',
    }


def effective_rule(rule: models.WorkshopEquipmentRule, equipment: models.SafetyEquipment) -> dict:
    """Código y gravedad vigentes: los del reglamento o, si faltan, los del catálogo."""
    return {
        'equipment_id': rule.equipment_id,
        'rule_code': rule.rule_code or equipment.missing_code,
        'severity': rule.severity or equipment.impact,
        'manual_verification': not equipment.model_class,
    }


def _workshop_response(db: Session, workshop: models.Workshop) -> dict:
    cameras = (
        db.query(Camera)
        .join(models.WorkshopCamera, models.WorkshopCamera.camera_id == Camera.id)
        .filter(models.WorkshopCamera.workshop_id == workshop.id)
        .order_by(Camera.id)
        .all()
    )
    rule_rows = (
        db.query(models.WorkshopEquipmentRule, models.SafetyEquipment)
        .join(models.SafetyEquipment, models.SafetyEquipment.id == models.WorkshopEquipmentRule.equipment_id)
        .filter(models.WorkshopEquipmentRule.workshop_id == workshop.id, models.WorkshopEquipmentRule.is_required.is_(True))
        .order_by(models.WorkshopEquipmentRule.equipment_id)
        .all()
    )
    required_ids = [rule.equipment_id for rule, _ in rule_rows]
    return {
        'id': workshop.id,
        'name': workshop.name,
        'laboratory_type': workshop.laboratory_type,
        'description': workshop.description,
        'cameras': [_camera_summary(camera) for camera in cameras],
        'required_equipment_ids': required_ids,
        'rules': [effective_rule(rule, equipment) for rule, equipment in rule_rows],
        'created_at': workshop.created_at,
        'updated_at': workshop.updated_at,
    }


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite devuelve fechas sin zona; se guardan en UTC, así que se marcan como UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


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
            'detected_at': _as_utc(row.detected_at),
        })
    for incident in incidents:
        if not result[incident.id]:
            result[incident.id].append({
                'id': None, 'equipment_id': incident.equipment_id, 'equipment_name': names.get(incident.equipment_id),
                'rule_code': incident.rule_code, 'severity': incident.severity,
                'has_capture': bool(incident.capture_path), 'detected_at': _as_utc(incident.occurred_at),
            })
    return result


def _incident_response(incident, workshop_name: str, camera_name: str, equipment_name: str | None,
                       reviewer_name: str | None = None, faults: list[dict] | None = None) -> dict:
    return {
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
        'occurred_at': _as_utc(incident.occurred_at),
        'rule_code': incident.rule_code,
        'has_capture': bool(incident.capture_path),
        'created_by_system': incident.created_by is None,
        'reviewed_by_name': reviewer_name,
        'reviewed_at': _as_utc(incident.reviewed_at),
        'review_reason': incident.review_reason,
        'action_taken': incident.action_taken,
        'faults': faults or [],
        **_escalation(incident),
    }


def _escalation(incident) -> dict:
    from app.modules.reglas_lab.incident_service import escalation_seconds
    if incident.status not in ('PENDIENTE', 'EN_REVISION') or incident.occurred_at is None:
        return {'pending_seconds': None, 'escalated': False}
    waited = (datetime.now(timezone.utc) - _as_utc(incident.occurred_at)).total_seconds()
    return {'pending_seconds': int(waited), 'escalated': waited >= escalation_seconds()}


@router.get('/configuration', response_model=schemas.RulesConfigurationResponse)
def get_configuration(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    permitted_area = {'ENCARGADO_QUIMICA': 'QUIMICA', 'ENCARGADO_FISICA': 'FISICA'}.get(_role(current_user))
    workshops_query = db.query(models.Workshop).filter(models.Workshop.is_active.is_(True))
    cameras_query = db.query(Camera).filter(Camera.is_active.is_(True))
    if permitted_area:
        workshops_query = workshops_query.filter(models.Workshop.laboratory_type == permitted_area)
        cameras_query = cameras_query.filter(Camera.laboratory_type == permitted_area)

    workshops = workshops_query.order_by(models.Workshop.name).all()
    catalog = db.query(models.SafetyEquipment).filter_by(is_active=True).order_by(models.SafetyEquipment.name).all()
    cameras = cameras_query.order_by(Camera.id).all()
    assigned_camera_ids = {row.camera_id for row in db.query(models.WorkshopCamera.camera_id).all()}

    return {
        'workshops': [_workshop_response(db, workshop) for workshop in workshops],
        'catalog': catalog,
        'available_cameras': [_camera_summary(camera) for camera in cameras if camera.id not in assigned_camera_ids],
    }


@router.post('/workshops', response_model=schemas.WorkshopResponse, status_code=status.HTTP_201_CREATED)
def create_workshop(
    payload: schemas.WorkshopCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_can_manage(current_user)
    permitted_area = {'ENCARGADO_QUIMICA': 'QUIMICA', 'ENCARGADO_FISICA': 'FISICA'}.get(_role(current_user))
    if permitted_area and payload.laboratory_type != permitted_area:
        raise HTTPException(status_code=403, detail='No puedes crear un taller para otra área')

    workshop = models.Workshop(
        name=payload.name.strip(),
        laboratory_type=payload.laboratory_type,
        description=payload.description,
    )
    db.add(workshop)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail='Ya existe un taller con ese nombre y área') from error
    db.refresh(workshop)
    return _workshop_response(db, workshop)


@router.put('/workshops/{workshop_id}/configuration', response_model=schemas.WorkshopResponse)
def update_workshop_configuration(
    workshop_id: int,
    payload: schemas.WorkshopConfigurationUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_can_manage(current_user)
    workshop = db.query(models.Workshop).filter_by(id=workshop_id, is_active=True).first()
    if not workshop:
        raise HTTPException(status_code=404, detail='Taller no encontrado')
    _ensure_workshop_access(workshop, current_user)

    camera_ids = set(payload.camera_ids)
    if payload.rules is not None:
        requested_rules = {rule.equipment_id: rule for rule in payload.rules}
    else:
        # Compatibilidad: solo identificadores; se conservan los códigos ya guardados.
        previous = {
            row.equipment_id: row
            for row in db.query(models.WorkshopEquipmentRule).filter_by(workshop_id=workshop_id).all()
        }
        requested_rules = {
            equipment_id: schemas.WorkshopRule(
                equipment_id=equipment_id,
                rule_code=previous[equipment_id].rule_code if equipment_id in previous else None,
                severity=previous[equipment_id].severity if equipment_id in previous else None,
            )
            for equipment_id in payload.required_equipment_ids
        }
    equipment_ids = set(requested_rules)
    cameras = db.query(Camera).filter(Camera.id.in_(camera_ids), Camera.is_active.is_(True)).all() if camera_ids else []
    if len(cameras) != len(camera_ids):
        raise HTTPException(status_code=400, detail='Una o más cámaras no existen o están inactivas')
    if any(camera.laboratory_type != workshop.laboratory_type for camera in cameras):
        raise HTTPException(status_code=400, detail='Las cámaras deben pertenecer al área del taller')

    assigned_elsewhere = (
        db.query(models.WorkshopCamera)
        .filter(models.WorkshopCamera.camera_id.in_(camera_ids), models.WorkshopCamera.workshop_id != workshop_id)
        .first()
        if camera_ids else None
    )
    if assigned_elsewhere:
        raise HTTPException(status_code=409, detail='Una cámara ya está asignada a otro taller')

    known_equipment = {
        row.id for row in db.query(models.SafetyEquipment.id)
        .filter(models.SafetyEquipment.id.in_(equipment_ids), models.SafetyEquipment.is_active.is_(True)).all()
    } if equipment_ids else set()
    if known_equipment != equipment_ids:
        raise HTTPException(status_code=400, detail='Uno o más equipos de seguridad no existen o están inactivos')

    previous_camera_ids = {
        row.camera_id for row in db.query(models.WorkshopCamera.camera_id).filter_by(workshop_id=workshop_id).all()
    }
    try:
        db.query(models.WorkshopCamera).filter_by(workshop_id=workshop_id).delete(synchronize_session=False)
        db.query(models.WorkshopEquipmentRule).filter_by(workshop_id=workshop_id).delete(synchronize_session=False)
        db.add_all(models.WorkshopCamera(workshop_id=workshop_id, camera_id=camera_id) for camera_id in camera_ids)
        db.add_all(
            models.WorkshopEquipmentRule(
                workshop_id=workshop_id, equipment_id=rule.equipment_id, is_required=True,
                rule_code=(rule.rule_code or '').strip() or None, severity=rule.severity,
            )
            for rule in requested_rules.values()
        )
        workshop.updated_at = datetime.now(timezone.utc)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail='No se pudo guardar la asignación de cámaras') from error

    # Con la práctica en curso, el análisis sigue la configuración nueva sin reiniciar la práctica.
    from app.modules.reglas_lab import monitoring
    monitoring.sync_workshop(db, workshop_id, previous_camera_ids)
    db.refresh(workshop)
    return _workshop_response(db, workshop)


@router.post('/equipment', response_model=schemas.SafetyEquipmentResponse, status_code=status.HTTP_201_CREATED)
def create_equipment(
    payload: schemas.SafetyEquipmentCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if _role(current_user) != 'ADMINISTRADOR':
        raise HTTPException(status_code=403, detail='Solo administradores pueden ampliar el catálogo EPP')
    equipment = models.SafetyEquipment(**payload.model_dump())
    db.add(equipment)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail='El identificador, nombre o código ya existe') from error
    db.refresh(equipment)
    return equipment


@router.get('/incidents', response_model=list[schemas.IncidentResponse])
def list_incidents(
    workshop_id: int | None = None,
    camera_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    incident_status: schemas.IncidentStatus | None = Query(None, alias='status'),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = (
        db.query(models.Incident, models.Workshop.name, Camera.name, models.SafetyEquipment.name, User.full_name)
        .outerjoin(models.Workshop, models.Workshop.id == models.Incident.workshop_id)
        .outerjoin(Camera, Camera.id == models.Incident.camera_id)  # la cámara puede haberse eliminado
        .outerjoin(models.SafetyEquipment, models.SafetyEquipment.id == models.Incident.equipment_id)
        .outerjoin(User, User.id == models.Incident.reviewed_by)
    )
    permitted_area = {'ENCARGADO_QUIMICA': 'QUIMICA', 'ENCARGADO_FISICA': 'FISICA'}.get(_role(current_user))
    if permitted_area:
        query = query.filter(func.coalesce(models.Incident.laboratory_type, models.Workshop.laboratory_type) == permitted_area)
    if workshop_id is not None:
        query = query.filter(models.Incident.workshop_id == workshop_id)
    if camera_id is not None:
        query = query.filter(models.Incident.camera_id == camera_id)
    if date_from:
        query = query.filter(models.Incident.occurred_at >= datetime.combine(date_from, time.min, timezone.utc))
    if date_to:
        query = query.filter(models.Incident.occurred_at < datetime.combine(date_to, time.max, timezone.utc))
    if incident_status:
        query = query.filter(models.Incident.status == incident_status)

    rows = query.order_by(models.Incident.occurred_at.desc()).limit(500).all()
    faults = _faults_by_incident(db, [row[0] for row in rows])
    return [
        _incident_response(incident, workshop_name, camera_name, equipment_name, reviewer_name, faults[incident.id])
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
    _ensure_workshop_access(workshop, current_user)
    camera_assignment = db.query(models.WorkshopCamera).filter_by(
        workshop_id=workshop.id,
        camera_id=payload.camera_id,
    ).first()
    if not camera_assignment:
        raise HTTPException(status_code=400, detail='La cámara no está asignada al taller indicado')
    if payload.equipment_id and not db.query(models.SafetyEquipment).filter_by(id=payload.equipment_id, is_active=True).first():
        raise HTTPException(status_code=400, detail='Equipo de seguridad desconocido')

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
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
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
        'occurred_at': _as_utc(incident.occurred_at),
    }


@router.patch('/incidents/{incident_id}/status', response_model=schemas.IncidentResponse)
def update_incident_status(
    incident_id: int,
    payload: schemas.IncidentStatusUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    incident = db.query(models.Incident).filter_by(id=incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail='Incidencia no encontrada')
    workshop = db.query(models.Workshop).filter_by(id=incident.workshop_id).first()
    _ensure_workshop_access(workshop, current_user)
    if incident.status in REVIEWED_STATUSES:
        raise HTTPException(status_code=409, detail='La incidencia ya fue validada')
    incident.status = payload.status
    db.commit()
    return _single_incident_response(db, incident, workshop)


REVIEWED_STATUSES = {'CONFIRMADA', 'DESCARTADA'}


def _single_incident_response(db: Session, incident, workshop) -> dict:
    camera_name = db.query(Camera.name).filter_by(id=incident.camera_id).scalar()
    equipment_name = db.query(models.SafetyEquipment.name).filter_by(id=incident.equipment_id).scalar()
    reviewer_name = db.query(User.full_name).filter_by(id=incident.reviewed_by).scalar() if incident.reviewed_by else None
    faults = _faults_by_incident(db, [incident])[incident.id]
    return _incident_response(incident, workshop.name, camera_name, equipment_name, reviewer_name, faults)


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
    _ensure_workshop_access(workshop, current_user)
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
    return _single_incident_response(db, incident, workshop)

# --------------------------------------------------------------------- monitoreo
def _workshop_for_monitoring(db: Session, workshop_id: int, current_user) -> models.Workshop:
    _ensure_can_manage(current_user)
    workshop = db.query(models.Workshop).filter_by(id=workshop_id, is_active=True).first()
    if not workshop:
        raise HTTPException(status_code=404, detail='Taller no encontrado')
    _ensure_workshop_access(workshop, current_user)
    return workshop


@router.get('/test-mode')
def get_test_mode(current_user=Depends(get_current_user)):
    from app.modules.reglas_lab.incident_service import test_mode_enabled
    return {'enabled': test_mode_enabled(), 'can_clear': test_mode_enabled() and _role(current_user) == 'ADMINISTRADOR'}


@router.post('/workshops/{workshop_id}/monitoring/start')
def start_monitoring(workshop_id: int, payload: schemas.MonitoringStart | None = None,
                     db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Inicia la práctica: analiza todas las cámaras del taller aunque nadie mire el video.

    Con clear_previous (fase de prueba, solo administrador) borra antes las incidencias,
    faltas y capturas de ese taller y reinicia el motor.
    """
    from app.modules.reglas_lab import monitoring
    from app.modules.reglas_lab.incident_service import clear_workshop_incidents, test_mode_enabled
    _workshop_for_monitoring(db, workshop_id, current_user)
    cleared = 0
    if payload and payload.clear_previous:
        if not test_mode_enabled():
            raise HTTPException(status_code=403, detail='El borrado de incidencias está desactivado (modo prueba apagado)')
        if _role(current_user) != 'ADMINISTRADOR':
            raise HTTPException(status_code=403, detail='Solo el administrador puede borrar incidencias de prueba')
        monitoring.stop_workshop(db, workshop_id)  # el motor también empieza de cero
        cleared = clear_workshop_incidents(db, workshop_id)
    return {'workshop_id': workshop_id, 'cleared_incidents': cleared,
            'cameras': monitoring.start_workshop(db, workshop_id)}


@router.post('/workshops/{workshop_id}/monitoring/stop')
def stop_monitoring(workshop_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    from app.modules.reglas_lab import monitoring
    _workshop_for_monitoring(db, workshop_id, current_user)
    return {'workshop_id': workshop_id, 'cameras': monitoring.stop_workshop(db, workshop_id)}


@router.get('/workshops/{workshop_id}/monitoring')
def monitoring_status(workshop_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    from app.modules.reglas_lab import monitoring
    workshop = db.query(models.Workshop).filter_by(id=workshop_id, is_active=True).first()
    if not workshop:
        raise HTTPException(status_code=404, detail='Taller no encontrado')
    _ensure_workshop_access(workshop, current_user)
    return {'workshop_id': workshop_id, 'active': monitoring.is_active(workshop_id),
            'cameras': monitoring.workshop_status(db, workshop_id)}


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
    _ensure_workshop_access(workshop, current_user)
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
