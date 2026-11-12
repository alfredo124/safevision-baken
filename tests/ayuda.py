"""Ayudas para las pruebas: encargados con su laboratorio asignado (desde el 2026-11-11 cada
encargado ve solo su laboratorio, ver app/core/permisos.py)."""
from app.core.security import get_password_hash
from app.modules.auth_db.models import User, UserRole
from app.modules.reglas_lab.models import Workshop, WorkshopCamera

ROL = {'QUIMICA': UserRole.ENCARGADO_QUIMICA, 'FISICA': UserRole.ENCARGADO_FISICA}


def encargado_con_laboratorio(db, username: str, laboratory_type: str, *, full_name: str | None = None,
                              workshop_id: int | None = None, camera_ids=()) -> int:
    """Crea o actualiza el encargado, le asigna un laboratorio (uno nuevo si no se indica) y pone
    en ese laboratorio las cámaras dadas. Devuelve el id del laboratorio."""
    if workshop_id is None:
        workshop = Workshop(name=f'Laboratorio de {username}', laboratory_type=laboratory_type)
        db.add(workshop)
        db.flush()
        workshop_id = workshop.id
    user = db.query(User).filter_by(username=username).first()
    if user is None:
        user = User(username=username, hashed_password=get_password_hash('x123456'),
                    full_name=full_name or username, role=ROL[laboratory_type])
        db.add(user)
    user.workshop_id = workshop_id
    for camera_id in camera_ids:
        if not db.query(WorkshopCamera).filter_by(camera_id=camera_id).first():
            db.add(WorkshopCamera(workshop_id=workshop_id, camera_id=camera_id))
    db.commit()
    return workshop_id
