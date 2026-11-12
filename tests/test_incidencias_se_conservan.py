"""Lo ocurrido en el laboratorio se guarda: iniciar una práctica nunca borra incidencias ni capturas.

Antes había una fase de prueba (`clear_previous`) que las borraba al iniciar; se eliminó el 2026-11-12.
"""
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


def headers(username, role):
    return {'Authorization': f"Bearer {create_access_token({'sub': username, 'role': role})}"}


class IncidenciasSeConservanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.capture_dir = Path(tempfile.mkdtemp(prefix='safevision-tests-conservar-'))
        cls._original_dir = capture.CAPTURE_DIR
        capture.CAPTURE_DIR = cls.capture_dir
        from tools.create_admin import create_admin
        create_admin('admin_conservar', 'admin-pass', 'Admin Conservar')
        with database.SessionLocal() as db:
            camera = Camera(name='Cam conservar', laboratory_type='QUIMICA', stream_url=tempfile.mktemp())
            workshop = models.Workshop(name='Química conservar', laboratory_type='QUIMICA')
            db.add_all([camera, workshop])
            db.commit()
            cls.workshop_id = workshop.id
            cls.capture_file = cls.capture_dir / 'conservar.jpg'
            cls.capture_file.write_bytes(b'x')
            incident = models.Incident(code='INC-CONSERVAR', workshop_id=workshop.id, camera_id=camera.id,
                                       title='t', description='d', severity='ALTO', capture_path=str(cls.capture_file))
            db.add(incident)
            db.flush()
            db.add(models.IncidentFault(incident_id=incident.id, equipment_id='guantes', severity='ALTO',
                                        capture_path=str(cls.capture_file)))
            db.commit()
        cls.admin = headers('admin_conservar', 'ADMINISTRADOR')

    @classmethod
    def tearDownClass(cls):
        capture.CAPTURE_DIR = cls._original_dir
        cls.client.__exit__(None, None, None)
        shutil.rmtree(cls.capture_dir, ignore_errors=True)

    def _count(self):
        with database.SessionLocal() as db:
            incidents = db.query(models.Incident).filter_by(workshop_id=self.workshop_id).count()
            faults = (db.query(models.IncidentFault)
                      .join(models.Incident, models.Incident.id == models.IncidentFault.incident_id)
                      .filter(models.Incident.workshop_id == self.workshop_id).count())
            return incidents, faults

    def test_iniciar_practica_no_borra_nada(self):
        base = f'/modules/reglas_lab/workshops/{self.workshop_id}/monitoring'
        # También si un navegador con la versión anterior aún envía clear_previous.
        for body in (None, {'clear_previous': True}):
            response = self.client.post(f'{base}/start', json=body, headers=self.admin)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn('cleared_incidents', response.json())
            self.client.post(f'{base}/stop', headers=self.admin)
        self.assertEqual(self._count(), (1, 1))
        self.assertTrue(self.capture_file.exists())

    def test_ya_no_existe_el_modo_prueba(self):
        self.assertEqual(self.client.get('/modules/reglas_lab/test-mode', headers=self.admin).status_code, 404)


if __name__ == '__main__':
    unittest.main()
