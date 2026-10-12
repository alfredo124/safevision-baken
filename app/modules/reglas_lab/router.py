from datetime import date, datetime, time, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.modules.ai_vision.models import Camera
from app.modules.auth_db.dependencies import get_current_user
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


def _workshop_response(db: Session, workshop: models.Workshop) -> dict:
    cameras = (
        db.query(Camera)
        .join(models.WorkshopCamera, models.WorkshopCamera.camera_id == Camera.id)
        .filter(models.WorkshopCamera.workshop_id == workshop.id)
        .order_by(Camera.id)
        .all()
    )
    required_ids = [
        row.equipment_id
        for row in db.query(models.WorkshopEquipmentRule)
        .filter_by(workshop_id=workshop.id, is_required=True)
        .all()
    ]
    return {
        'id': workshop.id,
        'name': workshop.name,
        'laboratory_type': workshop.laboratory_type,
        'description': workshop.description,
        'cameras': [_camera_summary(camera) for camera in cameras],
        'required_equipment_ids': required_ids,
        'created_at': workshop.created_at,
        'updated_at': workshop.updated_at,
    }


def _incident_response(incident, workshop_name: str, camera_name: str, equipment_name: str | None) -> dict:
    return {
        'id': incident.id,
        'code': incident.code,
        'workshop_id': incident.workshop_id,
        'workshop_name': workshop_name,
        'camera_id': incident.camera_id,
        'camera_name': camera_name,
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
        'occurred_at': incident.occurred_at,
    }


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
    equipment_ids = set(payload.required_equipment_ids)
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

    try:
        db.query(models.WorkshopCamera).filter_by(workshop_id=workshop_id).delete(synchronize_session=False)
        db.query(models.WorkshopEquipmentRule).filter_by(workshop_id=workshop_id).delete(synchronize_session=False)
        db.add_all(models.WorkshopCamera(workshop_id=workshop_id, camera_id=camera_id) for camera_id in camera_ids)
        db.add_all(
            models.WorkshopEquipmentRule(workshop_id=workshop_id, equipment_id=equipment_id, is_required=True)
            for equipment_id in equipment_ids
        )
        workshop.updated_at = datetime.now(timezone.utc)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail='No se pudo guardar la asignación de cámaras') from error

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
        db.query(models.Incident, models.Workshop.name, Camera.name, models.SafetyEquipment.name)
        .join(models.Workshop, models.Workshop.id == models.Incident.workshop_id)
        .join(Camera, Camera.id == models.Incident.camera_id)
        .outerjoin(models.SafetyEquipment, models.SafetyEquipment.id == models.Incident.equipment_id)
    )
    permitted_area = {'ENCARGADO_QUIMICA': 'QUIMICA', 'ENCARGADO_FISICA': 'FISICA'}.get(_role(current_user))
    if permitted_area:
        query = query.filter(models.Workshop.laboratory_type == permitted_area)
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

    return [
        _incident_response(incident, workshop_name, camera_name, equipment_name)
        for incident, workshop_name, camera_name, equipment_name in query.order_by(models.Incident.occurred_at.desc()).limit(500).all()
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
        'camera_name': db.query(Camera.name).filter_by(id=payload.camera_id).scalar(),
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
        'occurred_at': incident.occurred_at,
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
    incident.status = payload.status
    db.commit()
    camera_name = db.query(Camera.name).filter_by(id=incident.camera_id).scalar()
    equipment_name = db.query(models.SafetyEquipment.name).filter_by(id=incident.equipment_id).scalar()
    return _incident_response(incident, workshop.name, camera_name, equipment_name)