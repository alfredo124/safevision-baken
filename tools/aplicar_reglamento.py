"""Muestra la regla base de un reglamento y, solo con --confirmar, la aplica a un taller.

Uso:
  python tools/aplicar_reglamento.py                      # lista talleres y propuestas
  python tools/aplicar_reglamento.py quimica-v3.1 --taller 2              # muestra el cambio
  python tools/aplicar_reglamento.py quimica-v3.1 --taller 2 --confirmar  # lo guarda

Reemplaza los EPP requeridos del taller por los del reglamento. No toca cámaras.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.main import app  # noqa: F401  registra todos los modelos
from app.modules.reglas_lab.models import SafetyEquipment, Workshop, WorkshopEquipmentRule
from app.modules.reglas_lab.reglamentos import REGLAMENTOS
from app.modules.reglas_lab.services import initialize_reglas_lab


def apply(reglamento_id: str, workshop_id: int, confirm: bool) -> list[str]:
    reglamento = REGLAMENTOS[reglamento_id]
    lines = []
    with SessionLocal() as db:
        workshop = db.query(Workshop).filter_by(id=workshop_id).first()
        if not workshop:
            raise SystemExit(f'No existe el taller {workshop_id}')
        if workshop.laboratory_type != reglamento['laboratory_type']:
            raise SystemExit(f'El taller es de {workshop.laboratory_type} y el reglamento de {reglamento["laboratory_type"]}')
        catalog = {row.id: row for row in db.query(SafetyEquipment).all()}
        unknown = [rule['equipment_id'] for rule in reglamento['rules'] if rule['equipment_id'] not in catalog]
        if unknown:
            raise SystemExit(f'Equipos que no están en el catálogo: {unknown}')

        current = {row.equipment_id for row in db.query(WorkshopEquipmentRule).filter_by(workshop_id=workshop_id)}
        proposed = {rule['equipment_id'] for rule in reglamento['rules']}
        lines.append(f'Taller {workshop.id}: {workshop.name} ({workshop.laboratory_type})')
        lines.append(f'Reglamento: {reglamento["title"]} {reglamento["version"]}. {reglamento["note"]}')
        for rule in reglamento['rules']:
            equipment = catalog[rule['equipment_id']]
            kind = 'verificación manual' if not equipment.model_class else f'detector {equipment.model_class}'
            lines.append(f'  + {rule["rule_code"]} {equipment.name} [{rule["severity"]}, {kind}]')
        for equipment_id in sorted(current - proposed):
            lines.append(f'  - se quita {equipment_id} (no está en el reglamento)')

        if confirm:
            db.query(WorkshopEquipmentRule).filter_by(workshop_id=workshop_id).delete(synchronize_session=False)
            db.add_all(
                WorkshopEquipmentRule(workshop_id=workshop_id, equipment_id=rule['equipment_id'], is_required=True,
                                      rule_code=rule['rule_code'], severity=rule['severity'])
                for rule in reglamento['rules']
            )
            db.commit()
            lines.append('Guardado.')
        else:
            lines.append('Sin cambios. Repite con --confirmar para guardarlo.')
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('reglamento', nargs='?', choices=sorted(REGLAMENTOS))
    parser.add_argument('--taller', type=int)
    parser.add_argument('--confirmar', action='store_true')
    args = parser.parse_args()

    initialize_reglas_lab()  # asegura columnas nuevas y catálogo
    if not args.reglamento or args.taller is None:
        with SessionLocal() as db:
            for workshop in db.query(Workshop).order_by(Workshop.id):
                print(f'Taller {workshop.id}: {workshop.name} ({workshop.laboratory_type})')
        print('Reglamentos disponibles:', ', '.join(sorted(REGLAMENTOS)))
        return
    print('\n'.join(apply(args.reglamento, args.taller, args.confirmar)))


if __name__ == '__main__':
    main()
