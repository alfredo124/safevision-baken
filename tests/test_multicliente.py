"""Tarea 0B: varios clientes, miniaturas por imagen suelta, permisos por rol y CORS."""
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.modules.ai_vision import services
from app.modules.ai_vision.models import Camera
from app.modules.auth_db.models import User, UserRole
from tests.test_monitoring import make_processor, wait_until


def headers(username, role):
    return {'Authorization': f"Bearer {create_access_token({'sub': username, 'role': role})}"}


class SnapshotLeaseTest(unittest.TestCase):
    def test_snapshot_keeps_camera_open_only_while_requested(self):
        processor = make_processor()
        with mock.patch.object(services, 'SNAPSHOT_LEASE_SECONDS', 1.0):
            first = processor.snapshot(1, 'cam1')
            self.assertTrue(first and first[:2] == b'\xff\xd8', 'Devuelve un JPEG')
            self.assertEqual(len(processor.streams[1].subscriptions), 1)
            for _ in range(3):  # peticiones repetidas reutilizan la misma reserva
                time.sleep(0.4)
                processor.snapshot(1, 'cam1')
            self.assertEqual(len(processor.streams[1].subscriptions), 1)
            self.assertTrue(wait_until(lambda: 1 not in processor.streams, timeout=5),
                            'Sin peticiones, la cámara se cierra sola')

    def test_snapshot_shares_stream_with_video_viewers(self):
        processor = make_processor()
        viewer = processor.subscribe(1, 'cam1')
        processor.snapshot(1, 'cam1')
        self.assertEqual(len(processor.streams[1].subscriptions), 2)
        processor.unsubscribe(1, viewer)
        self.assertIn(1, processor.streams, 'La miniatura sigue viendo la cámara')
        processor.release_camera(1)

    def test_many_clients_one_capture(self):
        opens = []

        def factory(url):
            from tests.test_monitoring import FakeCapture
            opens.append(url)
            return FakeCapture(), False

        processor = services.VideoStreamProcessor(capture_factory=factory)
        tokens = [processor.subscribe(1, 'cam1') for _ in range(5)]
        processor.snapshot(1, 'cam1')
        self.assertEqual(len(opens), 1, 'Una sola captura por cámara, sin importar los clientes')
        for token in tokens:
            processor.unsubscribe(1, token)
        processor.release_camera(1)


class RolePermissionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        from tools.create_admin import create_admin
        create_admin('admin_test', 'admin-pass', 'Admin Pruebas')
        with database.SessionLocal() as db:
            for username, role in (('mc_quimica', UserRole.ENCARGADO_QUIMICA), ('mc_fisica', UserRole.ENCARGADO_FISICA)):
                if not db.query(User).filter_by(username=username).first():
                    db.add(User(username=username, hashed_password=get_password_hash('x123456'), full_name=username, role=role))
            chem = Camera(name='MC química', laboratory_type='QUIMICA', stream_url='mc-q')
            phys = Camera(name='MC física', laboratory_type='FISICA', stream_url='mc-f')
            db.add_all([chem, phys])
            db.commit()
            cls.chem, cls.phys = chem.id, phys.id
        cls.admin = headers('admin_test', 'ADMINISTRADOR')
        cls.quimica = headers('mc_quimica', 'ENCARGADO_QUIMICA')
        cls.fisica = headers('mc_fisica', 'ENCARGADO_FISICA')

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _camera_ids(self, who):
        return {camera['id'] for camera in self.client.get('/modules/ai_vision/cameras', headers=who).json()}

    def test_admin_sees_all_and_assistants_only_theirs(self):
        self.assertTrue({self.chem, self.phys} <= self._camera_ids(self.admin))
        self.assertIn(self.chem, self._camera_ids(self.quimica))
        self.assertNotIn(self.phys, self._camera_ids(self.quimica))
        self.assertIn(self.phys, self._camera_ids(self.fisica))
        self.assertNotIn(self.chem, self._camera_ids(self.fisica))

    def test_assistant_gets_403_on_other_laboratory(self):
        for url in (f'/modules/ai_vision/snapshot/{self.phys}', ):
            self.assertEqual(self.client.get(url, headers=self.quimica).status_code, 403)
            self.assertEqual(self.client.get(url).status_code, 401)
        token = create_access_token({'sub': 'mc_quimica', 'role': 'ENCARGADO_QUIMICA'})
        self.assertEqual(self.client.get(f'/modules/ai_vision/video_feed/{self.phys}?token={token}').status_code, 403)
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect) as closed:
            with self.client.websocket_connect(f'/modules/ai_vision/ws/detections/{self.phys}?token={token}') as socket:
                socket.receive_json()
        self.assertEqual(closed.exception.code, 4403)

    def test_snapshot_endpoint_returns_jpeg_for_own_camera(self):
        processor = make_processor()
        with mock.patch('app.modules.ai_vision.router.video_processor', processor):
            response = self.client.get(f'/modules/ai_vision/snapshot/{self.chem}', headers=self.quimica)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['content-type'], 'image/jpeg')
        processor.release_camera(self.chem)


class CorsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _preflight(self, origin):
        return self.client.options('/health', headers={'Origin': origin, 'Access-Control-Request-Method': 'GET'})

    def test_lan_origins_allowed(self):
        for origin in ('http://localhost:5173', 'http://192.168.1.20:5173', 'https://10.0.0.5:5173', 'http://172.20.1.2'):
            self.assertEqual(self._preflight(origin).headers.get('access-control-allow-origin'), origin, origin)

    def test_external_origin_rejected(self):
        for origin in ('https://evil.example', 'http://8.8.8.8:5173', 'http://192.168.1.20.evil.example'):
            self.assertIsNone(self._preflight(origin).headers.get('access-control-allow-origin'), origin)


if __name__ == '__main__':
    unittest.main()


class PortabilityTest(unittest.TestCase):
    def test_video_found_when_project_moved(self):
        name = next((services.PROJECT_ROOT / 'videos').glob('*.mp4'), None)
        if name is None:
            self.skipTest('Sin videos locales')
        moved = f'D:/otra/carpeta/videos/{name.name}'
        self.assertEqual(services.resolve_source(moved), str(name))
        self.assertEqual(services.resolve_source('0'), '0')
        self.assertEqual(services.resolve_source('rtsp://cam/stream'), 'rtsp://cam/stream')
        self.assertEqual(services.resolve_source('D:/no/existe.mp4'), 'D:/no/existe.mp4')

    def test_max_fps_from_environment(self):
        import os
        with mock.patch.dict(os.environ, {'SAFEVISION_MAX_FPS': '5'}):
            self.assertEqual(services._max_fps_from_env(), 5.0)
        with mock.patch.dict(os.environ, {'SAFEVISION_MAX_FPS': 'abc'}):
            self.assertEqual(services._max_fps_from_env(), 2.0)
