"""Bata abierta = falta leve; sin bata = falta grave (decisión del usuario, 2026-11-10).

El reglamento pide la bata abotonada. Una bata abierta se corrige en el momento,
por eso alerta con gravedad MEDIO; si la persona no lleva bata, alerta con la
gravedad de la regla. Datos simulados con pose, sin YOLO.
"""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import database
from app.main import app
from app.modules.ai_vision import capture
from app.modules.ai_vision.models import Camera
from app.modules.reglas_lab import models
from app.modules.reglas_lab.compliance import (
    DETAIL_COAT_OPEN, ComplianceEngine, ComplianceState, EquipmentDetection, EquipmentRule,
)
from app.modules.reglas_lab.incident_service import incident_faults, record_system_fault
from tests.test_compliance import run
from tests.test_compliance_pose import BOX, posed, torso_coat

COAT = EquipmentRule('chaleco', 'cuerpo', 'lab_coat', 'ALTO', 'RLQ-2.1')


def open_coat(box=BOX, confidence=0.6):
    """Dos mitades de bata a los lados: el centro del torso (polo) queda a la vista."""
    x1, y1, x2, y2 = box
    h = y2 - y1
    return [EquipmentDetection('lab_coat', (x1, y1 + 0.15 * h, x1 + 0.4 * (x2 - x1), y1 + 0.9 * h), confidence),
            EquipmentDetection('lab_coat', (x2 - 0.4 * (x2 - x1), y1 + 0.15 * h, x2, y1 + 0.9 * h), confidence)]


class OpenCoatEngineTest(unittest.TestCase):
    def test_closed_coat_complies(self):
        events, result = run(ComplianceEngine([COAT]), 20, posed(), lambda now: [torso_coat()])
        self.assertEqual(events, [])
        self.assertEqual(result.statuses[0].state, ComplianceState.COMPLIANT)

    def test_open_coat_is_a_minor_fault(self):
        events, result = run(ComplianceEngine([COAT]), 20, posed(), lambda now: open_coat())
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].severity, events[0].detail), ('MEDIO', DETAIL_COAT_OPEN))
        status = result.statuses[0]
        self.assertEqual((status.state, status.severity, status.detail),
                         (ComplianceState.VIOLATION, 'MEDIO', DETAIL_COAT_OPEN))

    def test_low_confidence_coat_counts_as_open(self):
        # Con bata abierta el modelo duda (0.4-0.7) aunque la caja cubra el torso.
        weak = lambda now: [replace(torso_coat(), confidence=0.5)]
        events, _ = run(ComplianceEngine([COAT]), 20, posed(), weak)
        self.assertEqual([(e.severity, e.detail) for e in events], [('MEDIO', DETAIL_COAT_OPEN)])

    def test_no_coat_is_a_serious_fault(self):
        events, result = run(ComplianceEngine([COAT]), 20, posed())
        self.assertEqual([(e.severity, e.detail) for e in events], [('ALTO', None)])
        self.assertEqual(result.statuses[0].severity, 'ALTO')

    def test_sporadic_false_coat_still_serious(self):
        # Sin bata el modelo a veces "ve" una con confianza media: la falta sigue siendo grave.
        flicker = lambda now: open_coat() if int(now * 2) % 2 else []
        events, _ = run(ComplianceEngine([COAT]), 20, posed(), flicker)
        self.assertEqual([(e.severity, e.detail) for e in events], [('ALTO', None)])

    def test_taking_off_open_coat_escalates(self):
        engine = ComplianceEngine([COAT])
        events, _ = run(engine, 20, posed(), lambda now: open_coat())
        later, _ = run(engine, 20, posed(), start=20.0)
        self.assertEqual([(e.severity, e.detail) for e in events + later],
                         [('MEDIO', DETAIL_COAT_OPEN), ('ALTO', None)])

    def test_closing_coat_clears_the_fault(self):
        engine = ComplianceEngine([COAT])
        run(engine, 20, posed(), lambda now: open_coat())
        events, result = run(engine, 20, posed(), lambda now: [torso_coat()], start=20.0)
        self.assertEqual(events, [])
        self.assertEqual(result.statuses[0].state, ComplianceState.COMPLIANT)


class OpenCoatIncidentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.capture_dir = Path(tempfile.mkdtemp(prefix='safevision-tests-bata-'))
        cls._original_dir = capture.CAPTURE_DIR
        capture.CAPTURE_DIR = cls.capture_dir

    @classmethod
    def tearDownClass(cls):
        capture.CAPTURE_DIR = cls._original_dir
        cls.client.__exit__(None, None, None)
        shutil.rmtree(cls.capture_dir, ignore_errors=True)

    def setUp(self):
        with database.SessionLocal() as db:
            camera = Camera(name='Cam bata', laboratory_type='QUIMICA', stream_url=tempfile.mktemp())
            workshop = models.Workshop(name=f'Química bata {tempfile.mktemp()[-6:]}', laboratory_type='QUIMICA')
            db.add_all([camera, workshop])
            db.commit()
            self.camera_id, self.workshop_id = camera.id, workshop.id

    def _record(self, severity, detail):
        from app.modules.reglas_lab.compliance import ViolationEvent
        event = ViolationEvent(1, 'chaleco', 'lab_coat', severity, 10.0, 2.0, BOX, 0.0,
                               rule_code='RLQ-2.1', detail=detail)
        with database.SessionLocal() as db:
            incident = record_system_fault(db, self.camera_id, self.workshop_id, event, (640, 480))
            return incident, incident_faults(db, incident)

    def test_open_coat_incident_says_so(self):
        incident, faults = self._record('MEDIO', DETAIL_COAT_OPEN)
        self.assertEqual(incident.severity, 'MEDIO')
        self.assertIn('abierta', incident.title)
        self.assertIn('abierta', incident.description)
        self.assertEqual(faults[0].detail, DETAIL_COAT_OPEN)

    def test_escalation_adds_serious_fault_to_same_alert(self):
        first, _ = self._record('MEDIO', DETAIL_COAT_OPEN)
        second, faults = self._record('ALTO', None)
        self.assertEqual(first.id, second.id)
        self.assertEqual([f.detail for f in faults], [DETAIL_COAT_OPEN, None])
        self.assertEqual(second.severity, 'ALTO')


if __name__ == '__main__':
    unittest.main()
