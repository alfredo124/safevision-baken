"""Lentes fuera de los ojos = falta leve (MEDIO); sin lentes = grave (decisión del usuario, 2026-11-11).

Igual que la bata abierta: el EPP está, pero mal puesto, y se corrige en el momento.
Datos simulados con pose, sin YOLO. Persona de 300 px de alto; la cara está a ~24 px del borde superior.
"""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import unittest

from app.modules.reglas_lab.compliance import (
    DETAIL_GOGGLES_OFF, ComplianceEngine, ComplianceState, EquipmentDetection, EquipmentRule,
)
from tests.test_compliance import run
from tests.test_compliance_pose import BOX, face_goggles, posed, skeleton

GOGGLES = EquipmentRule('lentes', 'ojos', 'goggles', 'ALTO', 'RLQ-2.2')


def lentes_en(x, y, confidence=0.8):
    return EquipmentDetection('goggles', (x - 20, y - 6, x + 20, y + 6), confidence)


class LentesMalPuestosTest(unittest.TestCase):
    def test_lentes_en_los_ojos_cumple(self):
        events, result = run(ComplianceEngine([GOGGLES]), 20, posed(), lambda now: [face_goggles()])
        self.assertEqual(events, [])
        self.assertEqual(result.statuses[0].state, ComplianceState.COMPLIANT)

    def test_lentes_sobre_la_cabeza_es_leve(self):
        x = (BOX[0] + BOX[2]) / 2
        events, result = run(ComplianceEngine([GOGGLES]), 20, posed(), lambda now: [lentes_en(x, BOX[1] - 4)])
        self.assertEqual([(e.severity, e.detail) for e in events], [('MEDIO', DETAIL_GOGGLES_OFF)])
        self.assertEqual((result.statuses[0].severity, result.statuses[0].detail), ('MEDIO', DETAIL_GOGGLES_OFF))

    def test_lentes_colgando_del_cuello_es_leve(self):
        hombro_y = skeleton()[5][1]
        events, _ = run(ComplianceEngine([GOGGLES]), 20, posed(), lambda now: [lentes_en((BOX[0] + BOX[2]) / 2, hombro_y)])
        self.assertEqual([(e.severity, e.detail) for e in events], [('MEDIO', DETAIL_GOGGLES_OFF)])

    def test_sin_lentes_es_grave(self):
        events, _ = run(ComplianceEngine([GOGGLES]), 20, posed())
        self.assertEqual([(e.severity, e.detail) for e in events], [('ALTO', None)])

    def test_lentes_lejos_de_la_persona_no_cuentan(self):
        # Unos lentes sobre una mesa al otro lado de la imagen no son de esta persona: falta grave.
        events, _ = run(ComplianceEngine([GOGGLES]), 20, posed(), lambda now: [lentes_en(600, 300)])
        self.assertEqual([(e.severity, e.detail) for e in events], [('ALTO', None)])

    def test_se_quita_los_lentes_de_la_frente_escala_a_grave(self):
        engine = ComplianceEngine([GOGGLES])
        x = (BOX[0] + BOX[2]) / 2
        primero, _ = run(engine, 20, posed(), lambda now: [lentes_en(x, BOX[1] - 4)])
        despues, _ = run(engine, 20, posed(), start=20.0)
        self.assertEqual([(e.severity, e.detail) for e in primero + despues],
                         [('MEDIO', DETAIL_GOGGLES_OFF), ('ALTO', None)])


if __name__ == '__main__':
    unittest.main()
