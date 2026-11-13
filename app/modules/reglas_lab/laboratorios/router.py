"""Rutas de laboratorios: configuración (laboratorios, catálogo de EPP y cámaras libres), alta y edición
de laboratorios, cámaras y EPP exigido por laboratorio, catálogo de EPP y locales (sedes).

Las URL no cambian: todas cuelgan de /modules/reglas_lab (ver reglas_lab/router.py)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.bitacora import registrar
from app.core.permisos import allowed_camera_ids, allowed_workshop_ids, is_admin
from app.modules.ai_vision.models import Camera
from app.modules.auth_db.dependencies import get_current_user, require_admin
from app.modules.reglas_lab import models
from app.modules.reglas_lab.comun import ensure_can_manage, ensure_workshop_access, get_db, rol
from app.modules.reglas_lab.laboratorios import schemas

router = APIRouter()


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


def _campus_name(db: Session, campus_id: int | None) -> str | None:
    campus = db.get(models.Campus, campus_id) if campus_id else None
    return campus.name if campus else None


def _check_campus(db: Session, campus_id: int | None) -> None:
    if campus_id is not None:
        campus = db.get(models.Campus, campus_id)
        if campus is None or not campus.is_active:
            raise HTTPException(status_code=400, detail='El local indicado no existe')


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
        'campus_id': workshop.campus_id,
        'campus_name': _campus_name(db, workshop.campus_id),
        'cameras': [_camera_summary(camera) for camera in cameras],
        'required_equipment_ids': required_ids,
        'rules': [effective_rule(rule, equipment) for rule, equipment in rule_rows],
        'created_at': workshop.created_at,
        'updated_at': workshop.updated_at,
    }


@router.get('/configuration', response_model=schemas.RulesConfigurationResponse)
def get_configuration(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    workshops_query = db.query(models.Workshop).filter(models.Workshop.is_active.is_(True))
    cameras_query = db.query(Camera).filter(Camera.is_active.is_(True))
    allowed = allowed_workshop_ids(current_user)
    if allowed is not None:
        # Encargado: su laboratorio. Las cámaras sin laboratorio las asigna el administrador.
        workshops_query = workshops_query.filter(models.Workshop.id.in_(allowed))
        cameras_query = cameras_query.filter(Camera.id.in_(allowed_camera_ids(db, current_user) or set()))

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
    # Los laboratorios pertenecen a un local: los crea el administrador general.
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail='Solo el administrador general crea laboratorios')

    _check_campus(db, payload.campus_id)
    workshop = models.Workshop(
        name=payload.name.strip(),
        laboratory_type=payload.laboratory_type,
        description=payload.description,
        campus_id=payload.campus_id,
    )
    db.add(workshop)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail='Ya existe un taller con ese nombre y área') from error
    db.refresh(workshop)
    registrar(db, current_user, 'CREAR_LABORATORIO', 'laboratorios',
              f'{workshop.name} ({workshop.laboratory_type})' + (f', local {_campus_name(db, workshop.campus_id)}'
                                                                if workshop.campus_id else ''))
    return _workshop_response(db, workshop)


@router.put('/workshops/{workshop_id}/configuration', response_model=schemas.WorkshopResponse)
def update_workshop_configuration(
    workshop_id: int,
    payload: schemas.WorkshopConfigurationUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage(current_user)
    workshop = db.query(models.Workshop).filter_by(id=workshop_id, is_active=True).first()
    if not workshop:
        raise HTTPException(status_code=404, detail='Taller no encontrado')
    ensure_workshop_access(workshop, current_user)

    camera_ids = set(payload.camera_ids)
    allowed_cameras = allowed_camera_ids(db, current_user)
    if allowed_cameras is not None and not camera_ids <= allowed_cameras:
        # Un encargado solo puede quitar o dejar cámaras de su laboratorio; las nuevas las asigna el administrador.
        raise HTTPException(status_code=403, detail='Solo el administrador puede asignar otras cámaras a este laboratorio')
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

    # Con la práctica en curso, la sesión guarda la nueva versión de las reglas y el análisis sigue la
    # configuración nueva sin reiniciar la práctica.
    from app.modules.reglas_lab.practicas import monitoreo, sesiones
    open_session = sesiones.open_session_for(db, workshop_id)
    if open_session is not None and sesiones.record_rules(db, open_session):
        db.commit()
    monitoreo.sync_workshop(db, workshop_id, previous_camera_ids)
    registrar(db, current_user, 'CONFIGURAR_LABORATORIO', 'laboratorios',
              f'{workshop.name}: cámaras {sorted(camera_ids) or "ninguna"}; EPP exigido: '
              + (', '.join(f'{rule.equipment_id} ({(rule.rule_code or "").strip() or "código del catálogo"})'
                           for rule in requested_rules.values()) or 'ninguno'))
    db.refresh(workshop)
    return _workshop_response(db, workshop)


@router.post('/equipment', response_model=schemas.SafetyEquipmentResponse, status_code=status.HTTP_201_CREATED)
def create_equipment(
    payload: schemas.SafetyEquipmentCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if rol(current_user) != 'ADMINISTRADOR':
        raise HTTPException(status_code=403, detail='Solo administradores pueden ampliar el catálogo EPP')
    equipment = models.SafetyEquipment(**payload.model_dump())
    db.add(equipment)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail='El identificador, nombre o código ya existe') from error
    db.refresh(equipment)
    registrar(db, current_user, 'CREAR_EPP', 'laboratorios',
              f'{equipment.name} ({equipment.missing_code}, {equipment.impact})')
    return equipment


def _campus_response(db: Session, campus: models.Campus) -> dict:
    count = db.query(models.Workshop).filter(models.Workshop.campus_id == campus.id,
                                             models.Workshop.is_active.is_(True)).count()
    return {'id': campus.id, 'name': campus.name, 'city': campus.city, 'address': campus.address,
            'is_active': campus.is_active, 'workshop_count': count}


@router.get('/campuses', response_model=list[schemas.CampusResponse])
def list_campuses(db: Session = Depends(get_db), _user=Depends(get_current_user)):
    return [_campus_response(db, campus) for campus in db.query(models.Campus).order_by(models.Campus.name).all()]


@router.post('/campuses', response_model=schemas.CampusResponse, status_code=status.HTTP_201_CREATED)
def create_campus(payload: schemas.CampusCreate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    campus = models.Campus(name=payload.name.strip(), city=payload.city, address=payload.address)
    db.add(campus)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail='Ya existe un local con ese nombre') from error
    db.refresh(campus)
    registrar(db, admin, 'CREAR_LOCAL', 'locales', campus.name)
    return _campus_response(db, campus)


@router.patch('/campuses/{campus_id}', response_model=schemas.CampusResponse)
def update_campus(campus_id: int, payload: schemas.CampusUpdate, db: Session = Depends(get_db),
                  admin=Depends(require_admin)):
    campus = db.get(models.Campus, campus_id)
    if campus is None:
        raise HTTPException(status_code=404, detail='Local no encontrado')
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(campus, field, value.strip() if isinstance(value, str) else value)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail='Ya existe un local con ese nombre') from error
    db.refresh(campus)
    registrar(db, admin, 'DESACTIVAR_LOCAL' if changes.get('is_active') is False else 'EDITAR_LOCAL', 'locales',
              f'{campus.name}: cambió {", ".join(changes) or "nada"}')
    return _campus_response(db, campus)


@router.patch('/workshops/{workshop_id}', response_model=schemas.WorkshopResponse)
def update_workshop(workshop_id: int, payload: schemas.WorkshopUpdate, db: Session = Depends(get_db),
                    admin=Depends(require_admin)):
    workshop = db.get(models.Workshop, workshop_id)
    if workshop is None or not workshop.is_active:
        raise HTTPException(status_code=404, detail='Taller no encontrado')
    changes = payload.model_dump(exclude_unset=True)
    if 'campus_id' in changes:
        _check_campus(db, changes['campus_id'])
    for field, value in changes.items():
        setattr(workshop, field, value.strip() if isinstance(value, str) else value)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail='Ya existe un taller con ese nombre y área') from error
    db.refresh(workshop)
    registrar(db, admin, 'EDITAR_LABORATORIO', 'laboratorios', f'{workshop.name}: cambió {", ".join(changes) or "nada"}')
    return _workshop_response(db, workshop)
