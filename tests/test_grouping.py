"""Varias faltas de la misma persona en poco tiempo forman una sola alerta con varias evidencias."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token
from app.main import app
from app.modules.ai_vision import capture
from app.modules.ai_vision.models import Camera
from app.modules.reglas_lab import models
from app.modules.reglas_lab.cumplimiento.motor import ViolationEvent
from app.modules.reglas_lab.incidencias.servicio import record_system_fault

BOX = (100.0, 100.0, 200.0, 400.0)
FAR = (400.0, 100.0, 500.0, 400.0)


def event(equipment_id, track_id=1, box=BOX, severity='ALTO', code='RLQ'):
    return ViolationEvent(track_id, equipment_id, equipment_id, severity, 10.0, 2.0, box, 0.0, rule_code=code)


class GroupingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.capture_dir = Path(tempfile.mkdtemp(prefix='safevision-tests-group-'))
        cls._original_dir = capture.CAPTURE_DIR
        capture.CAPTURE_DIR = cls.capture_dir
        from tools.create_admin import create_admin
        create_admin('admin_test', 'admin-pass', 'Admin Pruebas')
        cls.admin = {'Authorization': f"Bearer {create_access_token({'sub': 'admin_test', 'role': 'ADMINISTRADOR'})}"}

    @classmethod
    def tearDownClass(cls):
        capture.CAPTURE_DIR = cls._original_dir
        cls.client.__exit__(None, None, None)
        shutil.rmtree(cls.capture_dir, ignore_errors=True)

    def setUp(self):
        with database.SessionLocal() as db:
            camera = Camera(name='Cam agrupación', laboratory_type='QUIMICA', stream_url=tempfile.mktemp())
            workshop = models.Workshop(name=f'Química agrupación {tempfile.mktemp()[-6:]}', laboratory_type='QUIMICA')
            db.add_all([camera, workshop])
            db.commit()
            self.camera_id, self.workshop_id = camera.id, workshop.id

    def _record(self, ev, **kwargs):
        def writer(code):
            path = self.capture_dir / f'{code}.jpg'
            path.write_bytes(b'jpeg')
            return str(path)
        with database.SessionLocal() as db:
            return record_system_fault(db, self.camera_id, self.workshop_id, ev, (640, 480), writer, **kwargs).id

    def _alerts(self):
        items = self.client.get('/modules/reglas_lab/incidents', headers=self.admin).json()
        return [item for item in items if item['camera_id'] == self.camera_id]

    def test_same_person_faults_form_one_alert_with_one_capture_each(self):
        first = self._record(event('guantes', severity='MEDIO', code='RLQ-2.3'))
        second = self._record(event('lentes', track_id=9, code='RLQ-2.2'))  # cambió el ID, misma caja
        self.assertEqual(first, second)
        alerts = self._alerts()
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual([f['equipment_id'] for f in alert['faults']], ['guantes', 'lentes'])
        self.assertEqual([f['rule_code'] for f in alert['faults']], ['RLQ-2.3', 'RLQ-2.2'])
        self.assertTrue(all(f['has_capture'] for f in alert['faults']))
        self.assertEqual(alert['severity'], 'ALTO')  # la más grave
        self.assertTrue(alert['title'].startswith('Faltan 2 EPP'))
        for fault in alert['faults']:
            url = f"/modules/reglas_lab/incidents/{alert['id']}/faults/{fault['id']}/capture"
            self.assertEqual(self.client.get(url, headers=self.admin).status_code, 200)

    def test_repeated_fault_is_not_duplicated(self):
        first = self._record(event('guantes'))
        self.assertEqual(self._record(event('guantes')), first)
        self.assertEqual(len(self._alerts()[0]['faults']), 1)

    def test_other_person_gets_its_own_alert(self):
        first = self._record(event('guantes'))
        second = self._record(event('guantes', track_id=2, box=FAR))
        self.assertNotEqual(first, second)
        self.assertEqual(len(self._alerts()), 2)

    def test_faults_far_apart_in_time_are_separate_alerts(self):
        first = self._record(event('guantes'))
        second = self._record(event('lentes'), group_seconds=0)
        self.assertNotEqual(first, second)

    def test_reviewed_alert_is_not_reused(self):
        first = self._record(event('guantes'))
        self.client.post(f'/modules/reglas_lab/incidents/{first}/review',
                         json={'decision': 'CONFIRMADA', 'action': 'CORREGIDO'}, headers=self.admin)
        self.assertNotEqual(self._record(event('lentes')), first)

    def test_discard_deletes_every_capture(self):
        alert = self._record(event('guantes'))
        self._record(event('lentes'))
        before = list(self.capture_dir.iterdir())
        response = self.client.post(f'/modules/reglas_lab/incidents/{alert}/review',
                                    json={'decision': 'DESCARTADA'}, headers=self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any(f['has_capture'] for f in response.json()['faults']))
        self.assertTrue(all(not path.exists() for path in before if path.name.startswith(response.json()['code'])))

    def test_capture_path_cannot_be_passed_in_the_url(self):
        alert = self._record(event('guantes'))
        other = self.capture_dir / 'otra.jpg'
        other.write_bytes(b'otra')
        response = self.client.get(f'/modules/reglas_lab/incidents/{alert}/capture?capture_path={other}',
                                   headers=self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'jpeg')  # la de la incidencia, no la pedida


if __name__ == '__main__':
    unittest.main()


class SchemaRegressionTest(unittest.TestCase):
    """Las columnas de la tabla original de incidencias deben seguir en el modelo
    (una base real creada con versiones anteriores las exige, por ejemplo created_at NOT NULL)."""

    def test_original_incident_columns_are_kept(self):
        original = {'id', 'code', 'workshop_id', 'camera_id', 'equipment_id', 'title', 'description', 'severity',
                    'status', 'detected_class', 'confidence', 'bounding_box', 'frame_width', 'frame_height',
                    'occurred_at', 'created_by', 'created_at'}
        self.assertLessEqual(original, set(models.Incident.__table__.columns.keys()))
