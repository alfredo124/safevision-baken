"""El historial es de lo ocurrido, no de la cámara: guarda nombre de cámara, taller y laboratorio del
momento, y una cámara con incidencias se puede eliminar. SQLite temporal, sin YOLO."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.modules.ai_vision.models import Camera
from app.modules.auth_db.models import User, UserRole
from app.modules.reglas_lab import models
from app.modules.reglas_lab.compliance import ViolationEvent
from app.modules.reglas_lab.incident_service import record_system_fault
from app.modules.reglas_lab.services import snapshot_incident_names


def headers(username, role):
    return {'Authorization': f"Bearer {create_access_token({'sub': username, 'role': role})}"}


class CameraHistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        with database.SessionLocal() as db:
            for username, role in (('hist_quimica', UserRole.ENCARGADO_QUIMICA), ('hist_fisica', UserRole.ENCARGADO_FISICA)):
                if not db.query(User).filter_by(username=username).first():
                    db.add(User(username=username, hashed_password=get_password_hash('x123456'), full_name=username, role=role))
            db.commit()
        cls.quimica = headers('hist_quimica', 'ENCARGADO_QUIMICA')
        cls.fisica = headers('hist_fisica', 'ENCARGADO_FISICA')

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _camera_with_alert(self, name):
        with database.SessionLocal() as db:
            camera = Camera(name=name, laboratory_type='QUIMICA', stream_url=f'cam-{uuid4().hex[:6]}')
            workshop = models.Workshop(name=f'Química historial {uuid4().hex[:6]}', laboratory_type='QUIMICA')
            db.add_all([camera, workshop])
            db.flush()
            db.add(models.WorkshopCamera(workshop_id=workshop.id, camera_id=camera.id))
            db.commit()
            event = ViolationEvent(track_id=1, equipment_id='guantes', model_class='gloves', severity='ALTO', timestamp=0.0, started_at=0.0,
                                   present_ratio=0.0, person_box=(0, 0, 10, 10), rule_code='RLQ-2.3')
            incident = record_system_fault(db, camera.id, workshop.id, event, (640, 480))
            return camera.id, workshop.id, workshop.name, incident.id

    def _history(self, who=None):
        response = self.client.get('/modules/reglas_lab/incidents', headers=who or self.quimica)
        self.assertEqual(response.status_code, 200, response.text)
        return {item['id']: item for item in response.json()}

    def test_incident_stores_names_of_the_moment(self):
        camera_id, _, workshop_name, incident_id = self._camera_with_alert('Mesa 1')
        with database.SessionLocal() as db:
            db.query(Camera).filter_by(id=camera_id).update({'name': 'Mesa 1 renombrada'})
            db.commit()
        item = self._history()[incident_id]
        self.assertEqual(item['camera_name'], 'Mesa 1', 'El historial conserva el nombre que tenía la cámara')
        self.assertEqual(item['workshop_name'], workshop_name)

    def test_camera_with_history_can_be_deleted(self):
        camera_id, _, workshop_name, incident_id = self._camera_with_alert('Celular mesa 2')
        response = self.client.delete(f'/modules/ai_vision/cameras/{camera_id}', headers=self.quimica)
        self.assertEqual(response.status_code, 204, response.text)

        cameras = self.client.get('/modules/ai_vision/cameras', headers=self.quimica).json()
        self.assertNotIn(camera_id, [camera['id'] for camera in cameras], 'La cámara ya no aparece')
        self.assertEqual(self.client.get(f'/modules/ai_vision/snapshot/{camera_id}', headers=self.quimica).status_code, 404)
        self.assertEqual(self.client.delete(f'/modules/ai_vision/cameras/{camera_id}', headers=self.quimica).status_code, 404)

        item = self._history()[incident_id]
        self.assertEqual((item['camera_name'], item['workshop_name']), ('Celular mesa 2', workshop_name),
                         'El historial sigue completo tras eliminar la cámara')
        self.assertNotIn(incident_id, self._history(self.fisica), 'El área sigue protegida')
        with database.SessionLocal() as db:
            stored = db.query(Camera).filter_by(id=camera_id).first()
            self.assertFalse(stored.is_active)
            self.assertNotIn('cam-', stored.stream_url, 'Se borra la fuente de la cámara')
            self.assertEqual(db.query(models.WorkshopCamera).filter_by(camera_id=camera_id).count(), 0)
            new_camera = Camera(name='Nueva', laboratory_type='QUIMICA', stream_url='cam-nueva')
            db.add(new_camera)
            db.commit()
            self.assertNotEqual(new_camera.id, camera_id, 'El número no se reutiliza: no mezcla historiales')

    def test_camera_without_history_is_fully_deleted(self):
        with database.SessionLocal() as db:
            camera = Camera(name='Sin historial', laboratory_type='QUIMICA', stream_url='cam-sin-historial')
            db.add(camera)
            db.commit()
            camera_id = camera.id
        self.assertEqual(self.client.delete(f'/modules/ai_vision/cameras/{camera_id}', headers=self.quimica).status_code, 204)
        with database.SessionLocal() as db:
            self.assertIsNone(db.query(Camera).filter_by(id=camera_id).first())

    def test_old_incidents_get_current_names(self):
        camera_id, workshop_id, workshop_name, incident_id = self._camera_with_alert('Mesa antigua')
        with database.SessionLocal() as db:
            db.query(models.Incident).filter_by(id=incident_id).update(
                {'camera_name': None, 'workshop_name': None, 'laboratory_type': None})
            db.commit()
            self.assertGreaterEqual(snapshot_incident_names(db), 1)
            incident = db.query(models.Incident).filter_by(id=incident_id).first()
            self.assertEqual((incident.camera_name, incident.workshop_name, incident.laboratory_type),
                             ('Mesa antigua', workshop_name, 'QUIMICA'))


if __name__ == '__main__':
    unittest.main()
