"""Con la práctica en curso, el análisis sigue la configuración del taller sin reiniciarla:
cámara añadida -> se analiza; cámara quitada -> deja de analizarse; reglas cambiadas -> se aplican.
Cámara y detectores simulados; SQLite temporal."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import unittest
from unittest import mock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.modules.ai_vision.models import Camera
from app.modules.auth_db.models import User, UserRole
from app.modules.reglas_lab import models
from app.modules.reglas_lab.practicas import monitoreo
from tests.test_monitoring import make_processor, wait_until

BASE = '/modules/reglas_lab/workshops'


class LivePracticeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        with database.SessionLocal() as db:
            if not db.query(User).filter_by(username='admin_viva').first():
                db.add(User(username='admin_viva', hashed_password=get_password_hash('x123456'),
                            full_name='Admin viva', role=UserRole.ADMINISTRADOR))
                db.commit()
        cls.headers = {'Authorization': f"Bearer {create_access_token({'sub': 'admin_viva', 'role': 'ADMINISTRADOR'})}"}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        self.processor = make_processor()
        patcher = mock.patch.object(monitoreo, 'default_processor', self.processor)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _cameras(self, *names):
        with database.SessionLocal() as db:
            cameras = [Camera(name=name, laboratory_type='QUIMICA', stream_url=f'cam-viva-{uuid4().hex[:6]}') for name in names]
            db.add_all(cameras)
            workshop = models.Workshop(name=f'Química viva {uuid4().hex[:6]}', laboratory_type='QUIMICA')
            db.add(workshop)
            db.commit()
            return workshop.id, [camera.id for camera in cameras]

    def _configure(self, workshop_id, camera_ids, equipment):
        response = self.client.put(f'{BASE}/{workshop_id}/configuration', headers=self.headers,
                                   json={'camera_ids': camera_ids, 'required_equipment_ids': equipment})
        self.assertEqual(response.status_code, 200, response.text)

    def _running(self, camera_id):
        return self.processor.monitoring_status(camera_id) is not None

    def test_practice_follows_workshop_configuration(self):
        workshop_id, (first, second) = self._cameras('Mesa 1', 'Celular nuevo')
        self._configure(workshop_id, [first], ['guantes'])
        self.assertEqual(self.client.post(f'{BASE}/{workshop_id}/monitoring/start', headers=self.headers).status_code, 200)
        self.assertTrue(self._running(first))
        self.assertFalse(self._running(second))

        self._configure(workshop_id, [first, second], ['guantes'])  # se añade la cámara con la práctica en curso
        self.assertTrue(self._running(second), 'La cámara añadida se analiza sin reiniciar la práctica')
        self.assertTrue(self._running(first))

        self._configure(workshop_id, [second], ['guantes', 'lentes'])  # se quita una y se cambian las reglas
        self.assertTrue(wait_until(lambda: not self._running(first), timeout=3), 'La cámara quitada deja de analizarse')
        rules = {rule.equipment_id for rule in self.processor.streams[second].monitor.engine.rules}
        self.assertEqual(rules, {'guantes', 'lentes'}, 'Las reglas nuevas se aplican en la práctica')

        status = self.client.get(f'{BASE}/{workshop_id}/monitoring', headers=self.headers).json()
        self.assertTrue(status['active'])
        self.client.post(f'{BASE}/{workshop_id}/monitoring/stop', headers=self.headers)
        self.assertFalse(self._running(second))

    def test_without_practice_nothing_starts(self):
        workshop_id, (camera,) = self._cameras('Sin práctica')
        self._configure(workshop_id, [camera], ['guantes'])
        self.assertFalse(self._running(camera), 'Asignar una cámara no inicia el análisis si no hay práctica')
        status = self.client.get(f'{BASE}/{workshop_id}/monitoring', headers=self.headers).json()
        self.assertFalse(status['active'])

    def test_practice_started_without_cameras(self):
        workshop_id, (camera,) = self._cameras('Llega después')
        self._configure(workshop_id, [], ['guantes'])
        self.client.post(f'{BASE}/{workshop_id}/monitoring/start', headers=self.headers)
        self.assertTrue(self.client.get(f'{BASE}/{workshop_id}/monitoring', headers=self.headers).json()['active'],
                        'La práctica cuenta como iniciada aunque aún no tenga cámaras')
        self._configure(workshop_id, [camera], ['guantes'])
        self.assertTrue(self._running(camera))
        self.client.post(f'{BASE}/{workshop_id}/monitoring/stop', headers=self.headers)


if __name__ == '__main__':
    unittest.main()
