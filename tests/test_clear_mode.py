"""Fase de prueba: al iniciar la práctica, el administrador puede borrar las incidencias previas del taller."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token
from app.main import app
from app.modules.ai_vision import capture
from app.modules.ai_vision.models import Camera
from app.modules.reglas_lab import models


def headers(username, role):
    return {'Authorization': f"Bearer {create_access_token({'sub': username, 'role': role})}"}


class ClearOnStartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.capture_dir = Path(tempfile.mkdtemp(prefix='safevision-tests-clear-'))
        cls._original_dir = capture.CAPTURE_DIR
        capture.CAPTURE_DIR = cls.capture_dir
        from tools.create_admin import create_admin
        from app.core.security import get_password_hash
        from app.modules.auth_db.models import User, UserRole
        create_admin('admin_test', 'admin-pass', 'Admin Pruebas')
        with database.SessionLocal() as db:
            if not db.query(User).filter_by(username='clear_quimica').first():
                db.add(User(username='clear_quimica', hashed_password=get_password_hash('x123456'),
                            full_name='Encargado', role=UserRole.ENCARGADO_QUIMICA))
                db.commit()
        cls.admin = headers('admin_test', 'ADMINISTRADOR')
        cls.assistant = headers('clear_quimica', 'ENCARGADO_QUIMICA')

    @classmethod
    def tearDownClass(cls):
        capture.CAPTURE_DIR = cls._original_dir
        cls.client.__exit__(None, None, None)
        shutil.rmtree(cls.capture_dir, ignore_errors=True)

    def setUp(self):
        with database.SessionLocal() as db:
            camera = Camera(name='Cam limpieza', laboratory_type='QUIMICA', stream_url=tempfile.mktemp())
            target = models.Workshop(name=f'Química limpieza {tempfile.mktemp()[-6:]}', laboratory_type='QUIMICA')
            other = models.Workshop(name=f'Química otra {tempfile.mktemp()[-6:]}', laboratory_type='QUIMICA')
            db.add_all([camera, target, other])
            db.commit()
            self.camera_id, self.target, self.other = camera.id, target.id, other.id
            self.target_capture = self.capture_dir / f'{self.target}.jpg'
            self.target_capture.write_bytes(b'x')
            for workshop_id, path in ((self.target, str(self.target_capture)), (self.other, None)):
                incident = models.Incident(code=f'INC-CLR-{workshop_id}', workshop_id=workshop_id, camera_id=camera.id,
                                           title='t', description='d', severity='ALTO', capture_path=path)
                db.add(incident)
                db.flush()
                db.add(models.IncidentFault(incident_id=incident.id, equipment_id='guantes', severity='ALTO',
                                            capture_path=path))
            db.commit()

    def _count(self, workshop_id):
        with database.SessionLocal() as db:
            return db.query(models.Incident).filter_by(workshop_id=workshop_id).count()

    def _start(self, body, who):
        return self.client.post(f'/modules/reglas_lab/workshops/{self.target}/monitoring/start', json=body, headers=who)

    def test_admin_clears_only_that_workshop(self):
        response = self._start({'clear_previous': True}, self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['cleared_incidents'], 1)
        self.assertEqual(self._count(self.target), 0)
        self.assertEqual(self._count(self.other), 1, 'Otro taller no se toca')
        self.assertFalse(self.target_capture.exists(), 'Se borran también las capturas')
        with database.SessionLocal() as db:
            self.assertEqual(db.query(models.IncidentFault).join(
                models.Incident, models.Incident.id == models.IncidentFault.incident_id, isouter=True,
            ).filter(models.Incident.id.is_(None)).count(), 0, 'No quedan faltas huérfanas')

    def test_start_without_flag_keeps_data(self):
        self.assertEqual(self._start({}, self.admin).status_code, 200)
        self.assertEqual(self.client.post(f'/modules/reglas_lab/workshops/{self.target}/monitoring/start',
                                          headers=self.admin).status_code, 200)
        self.assertEqual(self._count(self.target), 1)

    def test_assistant_cannot_clear(self):
        self.assertEqual(self._start({'clear_previous': True}, self.assistant).status_code, 403)
        self.assertEqual(self._count(self.target), 1)

    def test_disabled_outside_test_mode(self):
        with mock.patch.dict(os.environ, {'SAFEVISION_MODO_PRUEBA': '0'}):
            self.assertEqual(self._start({'clear_previous': True}, self.admin).status_code, 403)
            self.assertFalse(self.client.get('/modules/reglas_lab/test-mode', headers=self.admin).json()['can_clear'])
        self.assertEqual(self._count(self.target), 1)
        self.assertTrue(self.client.get('/modules/reglas_lab/test-mode', headers=self.admin).json()['can_clear'])
        self.assertFalse(self.client.get('/modules/reglas_lab/test-mode', headers=self.assistant).json()['can_clear'])


if __name__ == '__main__':
    unittest.main()
