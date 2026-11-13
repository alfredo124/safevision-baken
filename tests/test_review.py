"""Tarea 2A: el encargado confirma o descarta una alerta. SQLite temporal."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.modules.ai_vision import capture
from app.modules.ai_vision.models import Camera
from app.modules.auth_db.models import User, UserRole
from app.modules.reglas_lab import models

BASE = '/modules/reglas_lab'


def headers(username, role):
    return {'Authorization': f"Bearer {create_access_token({'sub': username, 'role': role})}"}


class ReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.capture_dir = Path(tempfile.mkdtemp(prefix='safevision-tests-review-'))
        cls._original_dir = capture.CAPTURE_DIR
        capture.CAPTURE_DIR = cls.capture_dir
        with database.SessionLocal() as db:
            from tests.ayuda import encargado_con_laboratorio
            camera = Camera(name='Cam revisión', laboratory_type='QUIMICA', stream_url='cam-review')
            workshop = models.Workshop(name='Química revisión', laboratory_type='QUIMICA')
            db.add_all([camera, workshop])
            db.commit()
            cls.camera_id, cls.workshop_id = camera.id, workshop.id
            encargado_con_laboratorio(db, 'rev_quimica', 'QUIMICA', full_name='Ana Química',
                                      workshop_id=workshop.id, camera_ids=[camera.id])
            encargado_con_laboratorio(db, 'rev_fisica', 'FISICA', full_name='Luis Física')
        cls.quimica = headers('rev_quimica', 'ENCARGADO_QUIMICA')
        cls.fisica = headers('rev_fisica', 'ENCARGADO_FISICA')

    @classmethod
    def tearDownClass(cls):
        capture.CAPTURE_DIR = cls._original_dir
        cls.client.__exit__(None, None, None)
        shutil.rmtree(cls.capture_dir, ignore_errors=True)

    def _incident(self, with_capture=False):
        capture_path = None
        if with_capture:
            path = self.capture_dir / f'cap-{len(list(self.capture_dir.iterdir()))}.jpg'
            path.write_bytes(b'jpeg')
            capture_path = str(path)
        with database.SessionLocal() as db:
            incident = models.Incident(
                code=f'INC-REV-{tempfile.mktemp()[-8:]}', workshop_id=self.workshop_id, camera_id=self.camera_id,
                equipment_id='guantes', title='Guantes: no se detecta', description='d', severity='ALTO',
                rule_code='RLQ-2.3', capture_path=capture_path,
            )
            db.add(incident)
            db.commit()
            return incident.id, capture_path

    def _review(self, incident_id, body, who):
        return self.client.post(f'{BASE}/incidents/{incident_id}/review', json=body, headers=who)

    def test_confirm_records_reviewer_from_token_and_action(self):
        incident_id, _ = self._incident()
        response = self._review(incident_id, {'decision': 'CONFIRMADA', 'action': 'CORREGIDO',
                                              'reviewed_by_name': 'Otro nombre'}, self.quimica)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['status'], 'CONFIRMADA')
        self.assertEqual(body['reviewed_by_name'], 'Ana Química')  # del usuario autenticado
        self.assertEqual(body['action_taken'], 'CORREGIDO')
        self.assertTrue(body['reviewed_at'].endswith(('+00:00', 'Z')), body['reviewed_at'])

    def test_confirm_requires_action(self):
        incident_id, _ = self._incident()
        self.assertEqual(self._review(incident_id, {'decision': 'CONFIRMADA'}, self.quimica).status_code, 422)

    def test_other_laboratory_cannot_review(self):
        incident_id, _ = self._incident()
        response = self._review(incident_id, {'decision': 'DESCARTADA'}, self.fisica)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._review(incident_id, {'decision': 'DESCARTADA'}, {}).status_code, 401)

    def test_discard_deletes_capture_and_keeps_minimal_record(self):
        incident_id, capture_path = self._incident(with_capture=True)
        self.assertTrue(Path(capture_path).exists())
        response = self._review(incident_id, {'decision': 'DESCARTADA', 'reason': 'Lleva guantes transparentes'},
                                self.quimica)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'DESCARTADA')
        self.assertFalse(response.json()['has_capture'])
        self.assertIsNone(response.json()['action_taken'])
        self.assertFalse(Path(capture_path).exists())
        self.assertEqual(self.client.get(f'{BASE}/incidents/{incident_id}/capture', headers=self.quimica).status_code, 404)

    def test_cannot_review_twice_or_change_by_status_patch(self):
        incident_id, _ = self._incident()
        self._review(incident_id, {'decision': 'CONFIRMADA', 'action': 'RETIRADO_DE_PRACTICA'}, self.quimica)
        self.assertEqual(self._review(incident_id, {'decision': 'DESCARTADA'}, self.quimica).status_code, 409)
        # Ya no existe una ruta para cambiar el estado sin revisor ni acción: solo /review.
        other_id, _ = self._incident()
        for estado in ('PENDIENTE', 'ATENDIDO', 'CONFIRMADA'):
            patch = self.client.patch(f'{BASE}/incidents/{other_id}/status', json={'status': estado},
                                      headers=self.quimica)
            self.assertIn(patch.status_code, (404, 405), 'Cambiar el estado solo por /review')

    def test_pending_list_filters_by_area(self):
        incident_id, _ = self._incident()
        pending = self.client.get(f'{BASE}/incidents?status=PENDIENTE', headers=self.quimica).json()
        self.assertIn(incident_id, [item['id'] for item in pending])
        pending = self.client.get(f'{BASE}/incidents?status=PENDIENTE', headers=self.fisica).json()
        self.assertNotIn(incident_id, [item['id'] for item in pending])

    def test_delete_capture_refuses_paths_outside_folder(self):
        outside = Path(tempfile.mkdtemp()) / 'otro.jpg'
        outside.write_bytes(b'x')
        self.assertFalse(capture.delete_capture(str(outside), self.capture_dir))
        self.assertTrue(outside.exists())


if __name__ == '__main__':
    unittest.main()


class EscalationTest(unittest.TestCase):
    def test_old_pending_alert_is_escalated_but_never_auto_confirmed(self):
        import os
        from datetime import datetime, timedelta, timezone
        from unittest import mock
        client = TestClient(app)
        client.__enter__()
        from tools.create_admin import create_admin
        create_admin('admin_test', 'admin-pass', 'Admin Pruebas')
        admin = headers('admin_test', 'ADMINISTRADOR')
        with database.SessionLocal() as db:
            camera = Camera(name='Cam escalado', laboratory_type='QUIMICA', stream_url=tempfile.mktemp())
            workshop = models.Workshop(name=f'Química escalado {tempfile.mktemp()[-6:]}', laboratory_type='QUIMICA')
            db.add_all([camera, workshop])
            db.flush()
            old = models.Incident(code=f'INC-OLD-{tempfile.mktemp()[-6:]}', workshop_id=workshop.id, camera_id=camera.id,
                                  title='t', description='d', severity='ALTO',
                                  occurred_at=datetime.now(timezone.utc) - timedelta(minutes=6))
            new = models.Incident(code=f'INC-NEW-{tempfile.mktemp()[-6:]}', workshop_id=workshop.id, camera_id=camera.id,
                                  title='t', description='d', severity='ALTO')
            db.add_all([old, new])
            db.commit()
            old_id, new_id = old.id, new.id
        items = {item['id']: item for item in client.get(f'{BASE}/incidents', headers=admin).json()}
        self.assertTrue(items[old_id]['escalated'])
        self.assertEqual(items[old_id]['status'], 'PENDIENTE')  # nunca se confirma sola
        self.assertGreaterEqual(items[old_id]['pending_seconds'], 360)
        self.assertFalse(items[new_id]['escalated'])
        with mock.patch.dict(os.environ, {'SAFEVISION_ESCALAR_MIN': '10'}):
            items = {item['id']: item for item in client.get(f'{BASE}/incidents', headers=admin).json()}
            self.assertFalse(items[old_id]['escalated'])
        client.post(f'{BASE}/incidents/{old_id}/review', json={'decision': 'DESCARTADA'}, headers=admin)
        items = {item['id']: item for item in client.get(f'{BASE}/incidents', headers=admin).json()}
        self.assertFalse(items[old_id]['escalated'])
        client.__exit__(None, None, None)
