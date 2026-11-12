"""Bata por color del torso (2026-11-12): la confianza del modelo no separa una bata de otra prenda.

Medido con los videos del equipo y de stock: el modelo daba 0.3-0.7 de confianza tanto a un polo oscuro
(sin bata) como a una bata blanca de otra cámara. Con el umbral de confianza, las batas de otras cámaras
salían "abiertas" o "faltantes" (falsos positivos). El color del torso sí los separa.
"""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import unittest
from dataclasses import replace

import numpy as np

from app.modules.ai_vision.detectors import torso_light
from app.modules.reglas_lab.compliance import (
    DETAIL_COAT_OPEN, ComplianceEngine, ComplianceState, EquipmentDetection, PersonDetection, torso_for_color,
)
from tests.test_bata_abierta import COAT
from tests.test_compliance import run, state_of
from tests.test_compliance_pose import BOX, skeleton, torso_coat

DARK, MEDIUM, WHITE = 0.02, 0.6, 0.95


def person(light):
    return lambda now: [PersonDetection(1, BOX, 0.9, keypoints=skeleton(), torso_light=light)]


def coat(confidence):
    return lambda now: [replace(torso_coat(), confidence=confidence)]


class BataPorColorTest(unittest.TestCase):
    def test_white_coat_with_low_model_confidence_complies(self):
        """Bata de otra cámara: el modelo la ve con 0.45, pero el torso es blanco. Antes: "abierta"."""
        events, result = run(ComplianceEngine([COAT]), 30, person(WHITE), coat(0.45))
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'chaleco'), ComplianceState.COMPLIANT)

    def test_dark_shirt_detected_as_coat_is_a_serious_fault(self):
        """Polo oscuro que el modelo confunde con una bata (0.6): es falta grave, no "bata abierta"."""
        events, _ = run(ComplianceEngine([COAT]), 30, person(DARK), coat(0.6))
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0].detail)
        self.assertEqual(events[0].severity, COAT.severity)

    def test_partly_white_torso_is_an_open_coat(self):
        events, _ = run(ComplianceEngine([COAT]), 30, person(MEDIUM), coat(0.9))
        self.assertEqual([e.detail for e in events], [DETAIL_COAT_OPEN])

    def test_undetected_coat_on_white_torso_is_not_evidence(self):
        """El modelo no ve la bata (tapada por las manos, de lado) pero el torso es claro: no se alerta."""
        events, result = run(ComplianceEngine([COAT]), 60, person(WHITE))
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'chaleco'), ComplianceState.NOT_VERIFIABLE)

    def test_no_coat_and_dark_torso_still_alerts(self):
        events, _ = run(ComplianceEngine([COAT]), 30, person(DARK))
        self.assertEqual(len(events), 1)

    def test_without_color_data_uses_model_confidence(self):
        """Grabaciones antiguas o sin pose: se mantiene la regla anterior (confianza alta = cerrada)."""
        events, _ = run(ComplianceEngine([COAT]), 30, person(None), coat(0.45))
        self.assertEqual([e.detail for e in events], [DETAIL_COAT_OPEN])


class TorsoLightTest(unittest.TestCase):
    def _frame_with_torso(self, color):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        x1, y1, x2, y2 = (int(v) for v in torso_for_color(PersonDetection(1, BOX, 0.9, keypoints=skeleton())))
        frame[y1:y2 + 1, x1:x2 + 1] = color
        return frame

    def test_white_coat_vs_dark_shirt(self):
        detection = PersonDetection(1, BOX, 0.9, keypoints=skeleton())
        self.assertGreater(torso_light(self._frame_with_torso((235, 235, 235)), detection), 0.9)
        self.assertLess(torso_light(self._frame_with_torso((40, 30, 30)), detection), 0.1)
        # Una prenda clara pero de color (saturada) tampoco cuenta como bata blanca.
        self.assertLess(torso_light(self._frame_with_torso((200, 60, 40)), detection), 0.1)

    def test_no_keypoints_no_value(self):
        self.assertIsNone(torso_light(np.zeros((480, 640, 3), dtype=np.uint8), PersonDetection(1, BOX, 0.9)))


if __name__ == '__main__':
    unittest.main()
