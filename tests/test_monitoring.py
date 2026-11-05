"""Tarea 2 (y partes de 0B): monitoreo sin espectadores, incidencias con captura,
suscripciones por identificador. Cámara y detectores simulados; SQLite temporal."""
import shutil
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token
from app.main import app
from app.modules.ai_vision.capture import head_region, mask_heads, save_incident_capture
from app.modules.ai_vision.models import Camera
from app.modules.ai_vision.services import VideoStreamProcessor
from app.modules.reglas_lab import models, monitoring
from app.modules.reglas_lab.compliance import ComplianceConfig, FrameObservation, PersonDetection
from tests.test_compliance_pose import BOX, skeleton

FAST = ComplianceConfig(window_seconds=1.2, min_verified_seconds=0.9, min_verified_samples=3, cooldown_seconds=600)


class FakeCapture:
    """Cámara simulada: frames de 640x480 a ~50 FPS. open_delay simula una cámara lenta."""

    def __init__(self, open_delay=0.0):
        time.sleep(open_delay)
        self.released = False
        self.frame = np.full((480, 640, 3), 200, np.uint8)

    def isOpened(self):
        return True

    def read(self):
        time.sleep(0.02)
        return (not self.released), self.frame.copy()

    def get(self, prop):
        return 25.0 if prop == cv2.CAP_PROP_FPS else 0

    def set(self, *args):
        return True

    def release(self):
        self.released = True


class FakePipeline:
    """Una persona visible sin ningún EPP en cada frame."""

    def analyze(self, frame, timestamp, equipment_conf):
        person = PersonDetection(1, BOX, 0.9, keypoints=skeleton())
        return FrameObservation(timestamp, [person], [], (frame.shape[1], frame.shape[0]))


def make_processor(delays=None):
    delays = delays or {}
    return VideoStreamProcessor(
        capture_factory=lambda url: (FakeCapture(delays.get(url, 0.0)), False),
        pipeline_factory=FakePipeline,
    )


def wait_until(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class SubscriptionTest(unittest.TestCase):
    def test_monitoring_runs_without_viewers_and_survives_viewer_leaving(self):
        processor = make_processor()
        events = []
        rules = [models_rule('guantes', 'manos', 'gloves')]
        processor.start_monitoring(1, 'cam1', rules, lambda *args: events.append(args), config=FAST)
        viewer = processor.subscribe(1, 'cam1')
        processor.unsubscribe(1, viewer)
        self.assertTrue(wait_until(lambda: events), 'El monitoreo no generó eventos sin espectadores')
        status = processor.monitoring_status(1)
        self.assertEqual(status['viewers'], 0)
        self.assertGreater(status['analyses'], 0)
        time.sleep(1.5)
        self.assertEqual(len(events), 1, 'Debe haber una sola alerta por persona y EPP')
        self.assertTrue(processor.stop_monitoring(1))
        self.assertNotIn(1, processor.streams)

    def test_stale_unsubscribe_does_not_cut_new_stream(self):
        processor = make_processor()
        old = processor.subscribe(1, 'cam1')
        processor.release_camera(1)  # falla / reapertura
        new = processor.subscribe(1, 'cam1')
        processor.unsubscribe(1, old)  # baja atrasada del cliente antiguo
        self.assertIn(1, processor.streams)
        self.assertIn(new, processor.streams[1].subscriptions)
        processor.unsubscribe(1, new)
        self.assertNotIn(1, processor.streams)

    def test_opening_slow_camera_does_not_freeze_other_camera(self):
        processor = make_processor({'lenta': 1.5})
        token = processor.subscribe(1, 'rapida')
        self.assertTrue(wait_until(lambda: processor.get_frame(1, 0, timeout=1)[0] is not None))
        opener = threading.Thread(target=processor.subscribe, args=(2, 'lenta'))
        opener.start()
        time.sleep(0.2)  # la cámara lenta está abriéndose
        started = time.monotonic()
        frame, _, _ = processor.get_frame(1, processor.streams[1].jpeg_sequence, timeout=1)
        self.assertIsNotNone(frame)
        self.assertLess(time.monotonic() - started, 0.5)
        opener.join()
        processor.unsubscribe(1, token)
        processor.release_camera(2)

    def test_no_jpeg_encoding_without_viewers(self):
        processor = make_processor()
        processor.start_monitoring(1, 'cam1', [models_rule('guantes', 'manos', 'gloves')], lambda *a: None, config=FAST)
        time.sleep(0.5)
        self.assertEqual(processor.streams[1].jpeg_sequence, 0)
        processor.stop_monitoring(1)


def models_rule(equipment_id, slot, model_class):
    from app.modules.reglas_lab.compliance import EquipmentRule
    return EquipmentRule(equipment_id, slot, model_class, 'ALTO', rule_code='RLQ-2.3')


class CaptureMaskTest(unittest.TestCase):
    def test_heads_of_all_persons_are_masked(self):
        frame = np.full((480, 640, 3), 200, np.uint8)
        other = (400.0, 100.0, 500.0, 400.0)
        persons = [PersonDetection(1, BOX, 0.9, keypoints=skeleton()), PersonDetection(2, other, 0.9)]
        masked = mask_heads(frame, persons)
        nose = skeleton()[0]
        self.assertTrue((masked[int(nose[1]), int(nose[0])] == 40).all())
        self.assertTrue((masked[110, 450] == 40).all())  # sin puntos: franja superior de la caja
        self.assertTrue((frame == 200).all(), 'El original no se modifica')
        self.assertTrue((masked[350, 150] == 200).all(), 'El cuerpo queda visible')

    def test_region_stays_inside_frame(self):
        x1, y1, x2, y2 = head_region(PersonDetection(1, (-50, -50, 100, 300), 0.9), (480, 640, 3))
        self.assertGreaterEqual(x1, 0)
        self.assertGreaterEqual(y1, 0)


class MonitoringFlowTest(unittest.TestCase):
    """Con la página de Monitoreo cerrada, una persona sin guantes crea una sola incidencia con captura."""

    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.capture_dir = Path(tempfile.mkdtemp(prefix='safevision-tests-capturas-'))
        from tools.create_admin import create_admin
        create_admin('admin_test', 'admin-pass', 'Admin Pruebas')
        with database.SessionLocal() as db:
            camera = Camera(name='Cam pruebas monitoreo', laboratory_type='QUIMICA', stream_url='cam-monitoreo')
            physics_camera = Camera(name='Cam física', laboratory_type='FISICA', stream_url='cam-fisica')
            db.add_all([camera, physics_camera])
            db.flush()
            workshop = models.Workshop(name='Química monitoreo', laboratory_type='QUIMICA')
            db.add(workshop)
            db.flush()
            db.add(models.WorkshopCamera(workshop_id=workshop.id, camera_id=camera.id))
            db.add(models.WorkshopEquipmentRule(workshop_id=workshop.id, equipment_id='guantes',
                                                rule_code='RLQ-2.3', severity='ALTO'))
            db.add(models.WorkshopEquipmentRule(workshop_id=workshop.id, equipment_id='vestimenta'))
            db.commit()
            cls.camera_id, cls.workshop_id = camera.id, workshop.id

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        shutil.rmtree(cls.capture_dir, ignore_errors=True)

    def test_incident_created_with_masked_capture(self):
        processor = make_processor()
        processor_config = FAST

        original_start = processor.start_monitoring
        processor.start_monitoring = lambda *a, **k: original_start(*a, **{**k, 'config': processor_config})
        writer = lambda frame, persons, code, highlight_box=None: save_incident_capture(
            frame, persons, code, highlight_box, directory=self.capture_dir)

        with database.SessionLocal() as db:
            results = monitoring.start_workshop(db, self.workshop_id, processor=processor, capture_writer=writer)
        self.assertEqual(results[0]['started'], True, results)

        def incidents():
            with database.SessionLocal() as db:
                return db.query(models.Incident).filter_by(camera_id=self.camera_id).all()

        self.assertTrue(wait_until(lambda: incidents()), 'No se creó la incidencia')
        time.sleep(1.5)
        with database.SessionLocal() as db:
            monitoring.stop_workshop(db, self.workshop_id, processor=processor)
        found = incidents()
        self.assertEqual(len(found), 1)
        incident = found[0]
        self.assertIsNone(incident.created_by)
        self.assertEqual(incident.status, 'PENDIENTE')
        self.assertEqual(incident.rule_code, 'RLQ-2.3')
        self.assertEqual(incident.equipment_id, 'guantes')
        self.assertEqual([incident.frame_width, incident.frame_height], [640, 480])

        files = list(self.capture_dir.iterdir())
        self.assertEqual(len(files), 1, 'Solo se escribe la captura de la incidencia')
        self.assertTrue(files[0].name.startswith(incident.code))
        image = cv2.imread(str(files[0]))
        nose = skeleton()[0]
        self.assertLess(int(image[int(nose[1]), int(nose[0])].mean()), 60, 'La cabeza debe estar enmascarada')

        admin = {'Authorization': f"Bearer {create_access_token({'sub': 'admin_test', 'role': 'ADMINISTRADOR'})}"}
        listed = self.client.get('/modules/reglas_lab/incidents', headers=admin).json()
        mine = next(item for item in listed if item['id'] == incident.id)
        self.assertTrue(mine['has_capture'])
        self.assertTrue(mine['created_by_system'])

    def test_permissions(self):
        from tools.create_admin import create_admin
        from app.core.security import get_password_hash
        from app.modules.auth_db.models import User, UserRole
        create_admin('admin_test', 'admin-pass', 'Admin Pruebas')
        with database.SessionLocal() as db:
            if not db.query(User).filter_by(username='fisica_test').first():
                db.add(User(username='fisica_test', hashed_password=get_password_hash('x123456'),
                            full_name='Encargado Física', role=UserRole.ENCARGADO_FISICA))
                db.commit()
        physics = {'Authorization': f"Bearer {create_access_token({'sub': 'fisica_test', 'role': 'ENCARGADO_FISICA'})}"}
        base = f'/modules/reglas_lab/workshops/{self.workshop_id}/monitoring'
        self.assertEqual(self.client.post(f'{base}/start').status_code, 401)
        self.assertEqual(self.client.post(f'{base}/start', headers=physics).status_code, 403)
        self.assertEqual(self.client.get(base, headers=physics).status_code, 403)

        with database.SessionLocal() as db:
            incident = models.Incident(code='INC-TEST-PERM', workshop_id=self.workshop_id, camera_id=self.camera_id,
                                       title='t', description='d', severity='ALTO', capture_path='capturas/x.jpg')
            db.add(incident)
            db.commit()
            incident_id = incident.id
        url = f'/modules/reglas_lab/incidents/{incident_id}/capture'
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=physics).status_code, 403)


if __name__ == '__main__':
    unittest.main()


class IncidentTimezoneTest(unittest.TestCase):
    def test_occurred_at_is_returned_with_utc_offset(self):
        from datetime import datetime, timezone
        from app.modules.reglas_lab.router import _as_utc
        naive = datetime(2026, 11, 5, 13, 0, 0)
        self.assertEqual(_as_utc(naive).tzinfo, timezone.utc)
        self.assertEqual(_as_utc(naive).isoformat(), '2026-11-05T13:00:00+00:00')


class ReconnectTest(unittest.TestCase):
    def test_live_camera_reconnects_without_analysing_during_outage(self):
        from app.modules.ai_vision import services

        class DroppingCapture(FakeCapture):
            def __init__(self, reads_before_drop):
                super().__init__()
                self.left = reads_before_drop

            def read(self):
                time.sleep(0.02)
                if self.left is not None:
                    self.left -= 1
                    if self.left < 0:
                        return False, None  # la cámara deja de dar imagen
                return True, self.frame.copy()

        class Closed(FakeCapture):
            def isOpened(self):
                return False

        opens = []

        def factory(url):
            opens.append(time.monotonic())
            if len(opens) == 1:
                return DroppingCapture(reads_before_drop=40), False
            if len(opens) <= 3:
                return Closed(), False  # dos reintentos fallidos
            return DroppingCapture(reads_before_drop=None), False

        processor = VideoStreamProcessor(capture_factory=factory, pipeline_factory=FakePipeline)
        with unittest.mock.patch.object(services, 'RECONNECT_MAX_WAIT', 0.2),                 unittest.mock.patch.object(services, 'RECONNECT_FIRST_WAIT', 2.5):
            processor.start_monitoring(1, 'webcam', [models_rule('guantes', 'manos', 'gloves')], lambda *a: None,
                                       config=FAST)
            self.assertTrue(wait_until(lambda: processor.streams[1].reconnecting, timeout=5))
            self.assertTrue(processor.monitoring_status(1)['reconnecting'])
            # Puede analizarse una vez el último frame real previo al corte (intervalo 0.5 s).
            time.sleep(0.8)
            during = processor.streams[1].inference_count
            time.sleep(1.0)  # el primer reintento espera 2.5 s: seguimos sin señal
            self.assertTrue(processor.streams[1].reconnecting)
            self.assertEqual(processor.streams[1].inference_count, during, 'Sin análisis durante el corte')
            self.assertTrue(wait_until(lambda: not processor.streams[1].reconnecting, timeout=10))
            self.assertTrue(wait_until(lambda: processor.streams[1].inference_count > during + 1, timeout=5),
                            'Tras reconectar sigue analizando')
            self.assertGreaterEqual(len(opens), 4)
            self.assertIsNone(processor.streams[1].error)
            processor.stop_monitoring(1)


class CaptureDirTest(unittest.TestCase):
    def test_capture_dir_can_be_changed_by_environment(self):
        import importlib
        import os
        from app.modules.ai_vision import capture as capture_module
        other = tempfile.mkdtemp(prefix='safevision-tests-otra-')
        with unittest.mock.patch.dict(os.environ, {'SAFEVISION_CAPTURE_DIR': other}):
            reloaded = importlib.reload(capture_module)
            self.assertEqual(reloaded.CAPTURE_DIR, Path(other))
        importlib.reload(capture_module)
        shutil.rmtree(other, ignore_errors=True)
