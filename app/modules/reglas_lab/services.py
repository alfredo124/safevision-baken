from sqlalchemy.orm import Session

from app.core.database import Base, SessionLocal, engine
from app.modules.ai_vision.models import Camera
from app.modules.reglas_lab.models import SafetyEquipment, Workshop, WorkshopCamera, WorkshopEquipmentRule


DEFAULT_EQUIPMENT = [
    {'id': 'casco', 'name': 'Casco de Seguridad', 'slot': 'cabeza', 'icon': '🪖', 'missing_code': 'ERR-EPP-01', 'missing_description': 'Ausencia de casco en zona de riesgo de caída de objetos', 'impact': 'CRÍTICO', 'model_class': None},
    {'id': 'lentes', 'name': 'Lentes de Protección', 'slot': 'ojos', 'icon': '🥽', 'missing_code': 'ERR-EPP-02', 'missing_description': 'Uso omitido o inadecuado de lentes protectores antiproyección', 'impact': 'ALTO', 'model_class': 'goggles'},
    {'id': 'chaleco', 'name': 'Chaleco / Mandil', 'slot': 'cuerpo', 'icon': '🦺', 'missing_code': 'ERR-EPP-03', 'missing_description': 'Falta de indumentaria de alta visibilidad o mandil protector', 'impact': 'MEDIO', 'model_class': 'lab_coat'},
    {'id': 'arnes', 'name': 'Arnés anticaídas', 'slot': 'cuerpo', 'icon': '🪢', 'missing_code': 'ERR-EPP-04', 'missing_description': 'Operación en altura sin línea de vida o arnés normado', 'impact': 'CRÍTICO', 'model_class': None},
    {'id': 'guantes', 'name': 'Guantes Industriales', 'slot': 'manos', 'icon': '🧤', 'missing_code': 'ERR-EPP-05', 'missing_description': 'Manipulación de insumos químicos/térmicos sin guantes', 'impact': 'ALTO', 'model_class': 'gloves'},
    {'id': 'botas', 'name': 'Botas de Seguridad', 'slot': 'pies', 'icon': '🥾', 'missing_code': 'ERR-EPP-06', 'missing_description': 'Ingreso con calzado no normado o punta de acero', 'impact': 'MEDIO', 'model_class': None},
]


def initialize_reglas_lab() -> None:
    from app.modules.reglas_lab import models  # Register all module tables with shared metadata.

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.query(SafetyEquipment).count() == 0:
            db.add_all(SafetyEquipment(**equipment) for equipment in DEFAULT_EQUIPMENT)
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