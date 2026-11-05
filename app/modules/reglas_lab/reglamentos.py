"""Reglas base propuestas a partir de reglamentos leídos manualmente.

Son propuestas: no se aplican solas. El responsable las revisa y confirma
(tools/aplicar_reglamento.py --confirmar o, más adelante, desde la interfaz).
El PDF original no se guarda en el repositorio.
"""

REGLAMENTO_QUIMICA = {
    'id': 'quimica-v3.1',
    'title': 'Reglamento Interno del Laboratorio de Química',
    'version': '3.1 (octubre 2026)',
    'note': 'Plantilla sin nombre de institución; usar solo para pruebas hasta tener la versión oficial.',
    'laboratory_type': 'QUIMICA',
    'rules': [
        {'equipment_id': 'chaleco', 'rule_code': 'RLQ-2.1', 'severity': 'ALTO',
         'text': 'Bata de laboratorio de algodón, manga larga, limpia, abotonada y hasta las rodillas.'},
        {'equipment_id': 'lentes', 'rule_code': 'RLQ-2.2', 'severity': 'ALTO',
         'text': 'Gafas de seguridad con protección lateral, obligatorias desde el ingreso hasta la salida.'},
        {'equipment_id': 'guantes', 'rule_code': 'RLQ-2.3', 'severity': 'ALTO',
         'text': 'Guantes de nitrilo o del material adecuado según el reactivo.'},
        {'equipment_id': 'vestimenta', 'rule_code': 'RLQ-2.4', 'severity': 'ALTO',
         'text': 'Pantalón largo sin roturas, zapatos cerrados y cabello largo recogido.'},
    ],
}

REGLAMENTOS = {REGLAMENTO_QUIMICA['id']: REGLAMENTO_QUIMICA}
