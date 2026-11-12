from sqlalchemy.orm import Session

from app.core.database import Base, SessionLocal, add_missing_columns, engine
from app.modules.ai_vision.models import Camera
from app.modules.reglas_lab.models import SafetyEquipment, Workshop, WorkshopCamera, WorkshopEquipmentRule


DEFAULT_EQUIPMENT = [
    {'id': 'casco', 'name': 'Casco de Seguridad', 'slot': 'cabeza', 'icon': '🪖', 'missing_code': 'ERR-EPP-01', 'missing_description': 'Ausencia de casco en zona de riesgo de caída de objetos', 'impact': 'CRÍTICO', 'model_class': None},
    {'id': 'lentes', 'name': 'Lentes de Protección', 'slot': 'ojos', 'icon': '🥽', 'missing_code': 'ERR-EPP-02', 'missing_description': 'Uso omitido o inadecuado de lentes protectores antiproyección', 'impact': 'ALTO', 'model_class': 'goggles'},
    {'id': 'chaleco', 'name': 'Chaleco / Mandil', 'slot': 'cuerpo', 'icon': '🦺', 'missing_code': 'ERR-EPP-03', 'missing_description': 'Falta de indumentaria de alta visibilidad o mandil protector', 'impact': 'MEDIO', 'model_class': 'lab_coat'},
    {'id': 'arnes', 'name': 'Arnés anticaídas', 'slot': 'cuerpo', 'icon': '🪢', 'missing_code': 'ERR-EPP-04', 'missing_description': 'Operación en altura sin línea de vida o arnés normado', 'impact': 'CRÍTICO', 'model_class': None},
    {'id': 'guantes', 'name': 'Guantes Industriales', 'slot': 'manos', 'icon': '🧤', 'missing_code': 'ERR-EPP-05', 'missing_description': 'Manipulación de insumos químicos/térmicos sin guantes', 'impact': 'ALTO', 'model_class': 'gloves'},
    {'id': 'botas', 'name': 'Botas de Seguridad', 'slot': 'pies', 'icon': '🥾', 'missing_code': 'ERR-EPP-06', 'missing_description': 'Ingreso con calzado no normado o punta de acero', 'impact': 'MEDIO', 'model_class': None},
    # Sin detector: se muestra como "Requerido, verificación manual" y nunca genera alertas.
    {'id': 'vestimenta', 'name': 'Vestimenta adicional (zapatos cerrados, pantalón largo, cabello recogido)', 'slot': 'general', 'icon': '👞', 'missing_code': 'ERR-EPP-07', 'missing_description': 'Ingreso sin zapatos cerrados, pantalón largo o con el cabello suelto', 'impact': 'ALTO', 'model_class': None},
]


def snapshot_incident_names(db) -> int:
    """Incidencias anteriores sin nombres guardados: se copian los actuales (los mejores disponibles)."""
    from app.modules.ai_vision.models import Camera
    from app.modules.reglas_lab.models import Incident

    pending = db.query(Incident).filter(Incident.camera_name.is_(None) | Incident.workshop_name.is_(None)).all()
    for incident in pending:
        camera = db.query(Camera).filter_by(id=incident.camera_id).first()
        workshop = db.query(Workshop).filter_by(id=incident.workshop_id).first()
        incident.camera_name = incident.camera_name or (camera.name if camera else f'Cámara {incident.camera_id}')
        incident.workshop_name = incident.workshop_name or (workshop.name if workshop else f'Taller {incident.workshop_id}')
        incident.laboratory_type = incident.laboratory_type or (workshop.laboratory_type if workshop else None)
    if pending:
        db.commit()
    return len(pending)


def initialize_reglas_lab() -> None:
    from app.modules.reglas_lab import models  # Register all module tables with shared metadata.

    Base.metadata.create_all(bind=engine)
    add_missing_columns('lab_workshop_equipment_rules', {'rule_code': 'VARCHAR(40)', 'severity': 'VARCHAR(20)'})
    add_missing_columns('lab_incident_faults', {'detail': 'VARCHAR(40)'})
    add_missing_columns('lab_incidents', {
        'rule_code': 'VARCHAR(40)', 'capture_path': 'VARCHAR(300)',
        'reviewed_by': 'INTEGER', 'reviewed_at': 'DATETIME', 'review_reason': 'VARCHAR(300)',
        'action_taken': 'VARCHAR(30)', 'track_id': 'INTEGER',
        'camera_name': 'VARCHAR(120)', 'workshop_name': 'VARCHAR(120)', 'laboratory_type': 'VARCHAR(20)',
    })
    with SessionLocal() as db:
        snapshot_incident_names(db)
        # Solo se añaden los equipos que faltan; los existentes no se modifican.
        existing = {row.id for row in db.query(SafetyEquipment.id).all()}
        missing = [equipment for equipment in DEFAULT_EQUIPMENT if equipment['id'] not in existing]
        if missing:
            db.add_all(SafetyEquipment(**equipment) for equipment in missing)
            db.commit()

        if db.query(Workshop).count() == 0:
            welding = Workshop(
                name='Taller de Soldadura',
                laboratory_type='FISICA',
                description='Área de trabajo con procesos de soldadura y maquinaria.',
            )
            chemistry = Workshop(
                name='Laboratorio de Química',
                laboratory_type='QUIMICA',
                description='Área de prácticas y manipulación de reactivos químicos.',
            )
            db.add_all([welding, chemistry])
            db.flush()

            workshops_by_area = {'FISICA': welding, 'QUIMICA': chemistry}
            cameras = db.query(Camera).filter(Camera.is_active.is_(True)).all()
            db.add_all(
                WorkshopCamera(workshop_id=workshops_by_area[camera.laboratory_type].id, camera_id=camera.id)
                for camera in cameras
                if camera.laboratory_type in workshops_by_area
            )

            required_by_workshop = {
                welding.id: {'casco', 'lentes', 'chaleco', 'guantes', 'botas'},
                chemistry.id: {'lentes', 'chaleco', 'guantes', 'botas'},
            }
            db.add_all(
                WorkshopEquipmentRule(workshop_id=workshop_id, equipment_id=equipment_id, is_required=True)
                for workshop_id, equipment_ids in required_by_workshop.items()
                for equipment_id in equipment_ids
            )
            db.commit()