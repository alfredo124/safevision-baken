"""Mensaje del motor para dibujar recuadros en el frontend (Tarea 1C).

El video viaja limpio; este mensaje dice qué recuadro dibujar por persona, de qué
color y qué EPP falta, con identificadores y códigos de regla (las descripciones e
iconos los pone el frontend desde el catálogo). Es temporal: no se guarda.
"""
from __future__ import annotations

from app.modules.reglas_lab.compliance import ComplianceResult, ComplianceState, EquipmentRule, FrameObservation

COLORS = {
    'INCUMPLE': '#ef4444',
    'EN_RIESGO': '#f59e0b',
    'CUMPLE': '#10b981',
    'PARCIAL': '#cbd5e1',  # gris claro: persona presente, verificación parcial
}
# Color de la falta confirmada según la gravedad de la norma.
SEVERITY_COLORS = {'CRÍTICO': '#dc2626', 'ALTO': '#ef4444', 'MEDIO': '#f97316'}
SEVERITY_ORDER = {'CRÍTICO': 3, 'ALTO': 2, 'MEDIO': 1}


def build_overlay_message(camera_id: int, analysis_id: int, timestamp: float, observation: FrameObservation,
                          result: ComplianceResult, rules: list[EquipmentRule]) -> dict:
    by_id = {rule.equipment_id: rule for rule in rules}
    statuses: dict[int, list] = {}
    for status in result.statuses:
        statuses.setdefault(status.track_id, []).append(status)

    people = []
    for person in observation.persons:
        missing, at_risk, unverified, compliant, manual = [], [], [], [], []
        progress = 0.0
        for status in statuses.get(person.track_id, []):
            rule = by_id.get(status.equipment_id)
            if rule is None:
                continue
            entry = {'equipment_id': rule.equipment_id, 'rule_code': rule.rule_code}
            if not rule.model_class:
                manual.append(entry)  # verificación manual: no cambia el color
            elif status.state is ComplianceState.VIOLATION:
                missing.append({**entry, 'severity': rule.severity})
            elif status.state is ComplianceState.AT_RISK:
                at_risk.append(entry)
                progress = max(progress, status.progress)
            elif status.state is ComplianceState.COMPLIANT:
                compliant.append(rule.equipment_id)
            else:
                unverified.append(rule.equipment_id)

        if missing:
            severity = max((item['severity'] for item in missing), key=lambda value: SEVERITY_ORDER.get(value, 0))
            state, color = 'INCUMPLE', SEVERITY_COLORS.get(severity, COLORS['INCUMPLE'])
            label = 'Falta EPP: pendiente de validación'
            progress = 1.0
        elif at_risk:
            severity, state, color = None, 'EN_RIESGO', COLORS['EN_RIESGO']
            label = 'Verificando posible falta'
        elif compliant and not unverified:
            severity, state, color = None, 'CUMPLE', COLORS['CUMPLE']
            label = 'Cumple las reglas verificables'
        else:
            severity, state, color = None, 'PARCIAL', COLORS['PARCIAL']
            label = 'Verificación parcial' if compliant else 'Sin verificar'

        people.append({
            'track_id': person.track_id,
            'box': [round(value, 1) for value in person.box],
            'state': state,
            'severity': severity,
            'color': color,
            'label': label,
            'missing': missing,
            'at_risk': at_risk,
            'unverified': unverified,
            'manual': manual,
            'progress': round(progress, 2),
        })

    return {
        'camera_id': camera_id,
        'analysis_id': analysis_id,
        'timestamp': timestamp,
        'frame_size': list(observation.frame_size) if observation.frame_size else None,
        'monitoring': True,
        'people': people,
    }
