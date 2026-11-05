"""Tarea 0C: cámara desde el navegador (estación) por WebSocket. Sin YOLO real."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import time
import unittest
from unittest import mock

import cv2
import numpy as np
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core import database
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.modules.ai_vision import browser_ingest, services
from app.modules.ai_vision.models import Camera
from app.modules.auth_db.models import User, UserRole
from tests.test_monitoring import FAST, FakePipeline, models_rule, wait_until


def jpeg(value=120, size=(480, 640)):
    ok, buffer = cv2.imencode('.jpg', np.full((*size, 3), value, np.uint8))
    return buffer.tobytes()


def headers(username, role):
    return {'Authorization': f"Bearer {create_access_token({'sub': username, 'role': role})}"}


class BrowserCameraApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        with database.SessionLocal() as db:
            for username, role in (('bc_quimica', UserRole.ENCARGADO_QUIMICA), ('bc_fisica', UserRole.ENCARGADO_FISICA)):
                if not db.query(User).filter_by(username=username).first():
                    db.add(User(username=username, hashed_password=get_password_hash('x123456'), full_name=username, role=role))
            db.add(Camera(name='Archivo', laboratory_type='QUIMICA', stream_url='video.mp4'))
            db.commit()
            cls.file_camera = db.query(Camera).filter_by(stream_url='video.mp4').first().id
        cls.quimica = headers('bc_quimica', 'ENCARGADO_QUIMICA')
        cls.quimica_token = create_access_token({'sub': 'bc_quimica', 'role': 'ENCARGADO_QUIMICA'})
        cls.fisica_token = create_access_token({'sub': 'bc_fisica', 'role': 'ENCARGADO_FISICA'})

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _create(self, area='QUIMICA', who=None):
        response = self.client.post('/modules/ai_vision/cameras/browser', headers=who or self.quimica,
                                    json={'name': 'Celular mesa 1', 'laboratory_type': area})
        return response

    def _ingest(self, camera_id, token):
        return self.client.websocket_connect(f'/modules/ai_vision/ws/ingest/{camera_id}?token={token}')

    def test_create_browser_camera(self):
        response = self._create()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body['source_type'], 'BROWSER')
        self.assertTrue(body['stream_url'].startswith('browser:'))
        self.assertEqual(self._create('FISICA').status_code, 403, 'Química no crea cámaras de Física')

    def test_station_sends_frames_with_ack_and_snapshot_shows_them(self):
        camera = self._create().json()
        processor = services.VideoStreamProcessor()
        with mock.patch('app.modules.ai_vision.router.video_processor', processor):
            with self._ingest(camera['id'], self.quimica_token) as station:
                station.send_bytes(jpeg(200))
                self.assertEqual(station.receive_json()['ack'], 1)
                response = self.client.get(f"/modules/ai_vision/snapshot/{camera['id']}", headers=self.quimica)
                self.assertEqual(response.status_code, 200)
                shown = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_GRAYSCALE)
                self.assertGreater(shown.mean(), 150, 'La miniatura muestra lo que envía la estación')
                station.send_bytes(b'no es un jpeg')
                self.assertIn('error', station.receive_json())
        processor.release_camera(camera['id'])

    def test_only_one_station_per_camera(self):
        camera = self._create().json()
        with self._ingest(camera['id'], self.quimica_token) as first:
            first.send_bytes(jpeg())
            first.receive_json()
            with self.assertRaises(WebSocketDisconnect) as closed:
                with self._ingest(camera['id'], self.quimica_token) as second:
                    second.receive_json()
            self.assertEqual(closed.exception.code, 4409)
        slot = browser_ingest.get_slot(camera['stream_url'])
        self.assertFalse(slot.station_connected, 'Al cerrar, la cámara queda libre')

    def test_silent_station_does_not_block_reconnection(self):
        """El celular perdió la red sin cerrar la conexión: al volver, retoma su cámara (no queda "ocupada")."""
        camera = self._create().json()
        slot = browser_ingest.get_slot(camera['stream_url'])
        with self._ingest(camera['id'], self.quimica_token) as first:
            first.send_bytes(jpeg())
            first.receive_json()
            self.assertTrue(slot.busy())
            listed = {c['id']: c for c in self.client.get('/modules/ai_vision/cameras', headers=self.quimica).json()}
            self.assertTrue(listed[camera['id']]['station_connected'], 'La lista marca la cámara en uso')
            slot.active_at -= browser_ingest.FRAME_TIMEOUT + 1  # la primera conexión lleva un rato muda
            with self._ingest(camera['id'], self.quimica_token) as second:
                second.send_bytes(jpeg())
                self.assertEqual(second.receive_json()['ack'], 1, 'La reconexión toma la cámara')
                first.send_bytes(jpeg())
                with self.assertRaises(WebSocketDisconnect) as closed:
                    first.receive_json()
                self.assertEqual(closed.exception.code, 4409, 'La conexión antigua se cierra')
                self.assertTrue(slot.busy(), 'Cerrar la antigua no libera la de la nueva')
        self.assertFalse(slot.station_connected)

    def test_delete_camera_with_open_video_file(self):
        """En Windows el video puede seguir abierto al eliminar la cámara: se responde 204 y se borra después."""
        import tempfile
        from pathlib import Path
        from app.modules.ai_vision import router_cameras
        upload_dir = Path(tempfile.mkdtemp(prefix='safevision-tests-videos-'))  # nunca videos/ real
        self.enterContext(mock.patch.object(router_cameras, 'VIDEO_UPLOAD_DIR', upload_dir))
        video = upload_dir / 'prueba.mp4'
        video.write_bytes(b'video')
        with database.SessionLocal() as db:
            camera = Camera(name='Video abierto', laboratory_type='QUIMICA', stream_url=str(video))
            db.add(camera)
            db.commit()
            camera_id = camera.id
        real_unlink, calls = type(video).unlink, []

        def locked_once(path, missing_ok=False):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError('en uso')
            return real_unlink(path, missing_ok=missing_ok)
        with mock.patch.object(type(video), 'unlink', locked_once):
            response = self.client.delete(f'/modules/ai_vision/cameras/{camera_id}', headers=self.quimica)
            self.assertEqual(response.status_code, 204, response.text)
            self.assertTrue(wait_until(lambda: not video.exists(), timeout=5), 'Se borra al liberarse')

    def test_permissions_and_source_type(self):
        camera = self._create().json()
        for token, code, camera_id in ((self.fisica_token, 4403, camera['id']), ('basura', 4401, camera['id']),
                                       (self.quimica_token, 4400, self.file_camera)):
            with self.assertRaises(WebSocketDisconnect) as closed:
                with self._ingest(camera_id, token) as socket:
                    socket.receive_json()
            self.assertEqual(closed.exception.code, code)


class BrowserStreamTest(unittest.TestCase):
    """La cámara del navegador pasa por el mismo análisis; sin señal no se analiza."""

    def test_monitoring_waits_for_station_and_resumes(self):
        url = f'browser:test-{time.monotonic_ns()}'
        slot = browser_ingest.get_slot(url)
        processor = services.VideoStreamProcessor(pipeline_factory=FakePipeline)
        frame = np.full((480, 640, 3), 90, np.uint8)
        with mock.patch.object(browser_ingest, 'FRAME_TIMEOUT', 0.5):
            processor.start_monitoring(1, url, [models_rule('guantes', 'manos', 'gloves')], lambda *a: None, config=FAST)
            self.assertTrue(wait_until(lambda: processor.streams[1].reconnecting, timeout=3), 'Sin estación: sin señal')
            time.sleep(0.6)
            self.assertEqual(processor.streams[1].inference_count, 0, 'Sin frames no se analiza')

            slot.station_connected = True
            stop = time.monotonic() + 3
            while time.monotonic() < stop and processor.streams[1].inference_count == 0:
                slot.push(frame)
                time.sleep(0.1)
            self.assertGreater(processor.streams[1].inference_count, 0, 'Con la estación transmitiendo, se analiza')
            self.assertFalse(processor.streams[1].reconnecting)

            slot.station_connected = False  # la estación se desconecta
            self.assertTrue(wait_until(lambda: processor.streams[1].reconnecting, timeout=3))
            processor.stop_monitoring(1)

    def test_ingest_rate_is_limited(self):
        slot = browser_ingest.BrowserFrameSlot()
        frame = np.zeros((10, 10, 3), np.uint8)
        results = [slot.push(frame) for _ in range(5)]  # cinco seguidos, sin esperar
        self.assertEqual(results[0], True)
        self.assertIn(False, results, 'Los frames demasiado seguidos se descartan')

    def test_oversized_frame_rejected(self):
        self.assertIsNone(browser_ingest.decode_jpeg(b'x' * (browser_ingest.MAX_FRAME_BYTES + 1)))
        self.assertIsNotNone(browser_ingest.decode_jpeg(jpeg()))


if __name__ == '__main__':
    unittest.main()
