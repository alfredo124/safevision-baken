"""Tarea 1C: estado EN_RIESGO, mensaje de recuadros y WebSocket. Datos simulados."""
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.modules.reglas_lab.compliance import (
    ComplianceEngine, ComplianceState, EquipmentRule, FrameObservation, PersonDetection,
)
from app.modules.reglas_lab.overlay import build_overlay_message
from tests.test_compliance_pose import BOX, posed, skeleton, wrist_glove, face_goggles
from tests.test_compliance import run
from tests.test_monitoring import FAST, make_processor, wait_until

GLOVES = EquipmentRule('guantes', 'manos', 'gloves', 'ALTO', rule_code='RLQ-2.3')
GOGGLES = EquipmentRule('lentes', 'ojos', 'goggles', 'ALTO', rule_code='RLQ-2.2')
CLOTHING = EquipmentRule('vestimenta', 'general', None, 'ALTO', rule_code='RLQ-2.4')
RULES = [GLOVES, GOGGLES, CLOTHING]


def message_after(seconds, persons, equipment=lambda now: []):
    engine = ComplianceEngine(RULES)
    _, result = run(engine, seconds, persons, equipment)
    observation = FrameObservation(seconds, persons(seconds), equipment(seconds), (640, 480))
    return build_overlay_message(1, 42, 1730812345.5, observation, result, RULES)


class RiskStateTest(unittest.TestCase):
    def test_absence_goes_through_at_risk_before_violation(self):
        engine = ComplianceEngine([GLOVES])
        states = []
        for index in range(30):
            now = index / 2
            result = engine.update(FrameObservation(now, posed()(now), [], (640, 480)))
            states.append((result.statuses[0].state, result.statuses[0].progress))
        names = [state for state, _ in states]
        first_risk = names.index(ComplianceState.AT_RISK)
        first_violation = names.index(ComplianceState.VIOLATION)
        self.assertLess(first_risk, first_violation)
        progress = [p for state, p in states if state is ComplianceState.AT_RISK]
        self.assertEqual(progress, sorted(progress), 'El progreso hacia la falta no retrocede')


class OverlayMessageTest(unittest.TestCase):
    def test_violation_message(self):
        message = message_after(20, posed())
        self.assertEqual(message['frame_size'], [640, 480])
        self.assertEqual(message['analysis_id'], 42)
        person = message['people'][0]
        self.assertEqual(person['state'], 'INCUMPLE')
        self.assertEqual(person['color'], '#ef4444')
        self.assertEqual({m['rule_code'] for m in person['missing']}, {'RLQ-2.3', 'RLQ-2.2'})
        self.assertEqual(person['box'], list(BOX))
        self.assertEqual([m['equipment_id'] for m in person['manual']], ['vestimenta'])

    def test_compliant_is_green_even_with_manual_rule(self):
        everything = lambda now: [wrist_glove(side=0), wrist_glove(side=1), face_goggles()]
        person = message_after(20, posed(), everything)['people'][0]
        self.assertEqual(person['state'], 'CUMPLE')
        self.assertEqual(person['color'], '#10b981')
        self.assertEqual(person['missing'], [])

    def test_hidden_hands_is_partial_not_green(self):
        person = message_after(20, posed(wrists=(0.1, 0.1)), lambda now: [face_goggles()])['people'][0]
        self.assertEqual(person['state'], 'PARCIAL')
        self.assertIn('guantes', person['unverified'])

    def test_at_risk_message(self):
        person = message_after(4, posed(), lambda now: [face_goggles()])['people'][0]
        self.assertEqual(person['state'], 'EN_RIESGO')
        self.assertEqual(person['color'], '#f59e0b')
        self.assertEqual(person['at_risk'][0]['rule_code'], 'RLQ-2.3')
        self.assertGreater(person['progress'], 0)


class CleanVideoTest(unittest.TestCase):
    def test_no_analysis_without_practice(self):
        processor = make_processor()
        token = processor.subscribe(1, 'cam1')
        time.sleep(0.8)
        self.assertEqual(processor.streams[1].inference_count, 0)
        self.assertIsNone(processor.latest_overlay(1))
        processor.unsubscribe(1, token)

    def test_overlay_published_while_monitoring(self):
        processor = make_processor()
        processor.start_monitoring(1, 'cam1', [GLOVES], lambda *a: None, config=FAST)
        self.assertTrue(wait_until(lambda: processor.latest_overlay(1) is not None))
        overlay = processor.latest_overlay(1)
        self.assertEqual(overlay['camera_id'], 1)
        self.assertEqual(overlay['frame_size'], [640, 480])
        processor.stop_monitoring(1)
        self.assertIsNone(processor.latest_overlay(1))


class WebSocketTest(unittest.TestCase):
    def test_sends_overlay_once_per_analysis_and_idle_notice(self):
        from app.core import database
        from app.modules.ai_vision.models import Camera
        from tools.create_admin import create_admin
        client = TestClient(app)
        client.__enter__()
        create_admin('admin_test', 'admin-pass', 'Admin Pruebas')
        with database.SessionLocal() as db:
            camera = Camera(name='Cam WS', laboratory_type='QUIMICA', stream_url='cam-ws')
            db.add(camera)
            db.commit()
            camera_id = camera.id
        token = create_access_token({'sub': 'admin_test', 'role': 'ADMINISTRADOR'})
        overlay = {'camera_id': camera_id, 'analysis_id': 7, 'monitoring': True, 'people': [], 'frame_size': [640, 480]}
        with mock.patch('app.modules.ai_vision.router.video_processor') as processor:
            processor.latest_overlay.return_value = overlay
            processor.monitoring_status.return_value = {'sync_delay': 1.2, 'analysis_rate': 0.9, 'started_at': 1.0}
            with client.websocket_connect(f'/modules/ai_vision/ws/detections/{camera_id}?token={token}') as socket:
                self.assertEqual(socket.receive_json()['analysis_id'], 7)
                processor.latest_overlay.return_value = None
                processor.monitoring_status.return_value = None
                idle = socket.receive_json()
                self.assertEqual((idle['monitoring'], idle['people']), (False, []))
        client.__exit__(None, None, None)


if __name__ == '__main__':
    unittest.main()


class SyncTest(unittest.TestCase):
    """Con análisis lento, el recuadro publicado coincide con el frame que se muestra."""

    def test_video_is_delayed_to_match_overlay(self):
        import cv2
        import numpy as np
        from tests.test_monitoring import FakeCapture
        from app.modules.ai_vision.services import VideoStreamProcessor

        class CountingCapture(FakeCapture):
            counter = 0

            def read(self):
                time.sleep(0.02)
                CountingCapture.counter = (CountingCapture.counter + 1) % 250
                return True, np.full((48, 64, 3), CountingCapture.counter, np.uint8)

        values = {}

        class SlowPipeline:
            def analyze(self, frame, timestamp, conf):
                values[timestamp] = int(frame[0, 0, 0])
                time.sleep(0.5)  # análisis lento, como en la CPU real
                return FrameObservation(timestamp, [PersonDetection(1, (5, 5, 40, 45), 0.9)], [], (64, 48))

        processor = VideoStreamProcessor(capture_factory=lambda url: (CountingCapture(), False),
                                         pipeline_factory=SlowPipeline)
        processor.start_monitoring(1, 'cam', [GLOVES], lambda *a: None, config=FAST)
        viewer = processor.subscribe(1, 'cam')
        time.sleep(2.5)  # que el retraso se ajuste
        gaps, last_id, deadline = [], None, time.monotonic() + 4
        while time.monotonic() < deadline:
            overlay = processor.latest_overlay(1)
            if overlay and overlay['analysis_id'] != last_id:
                last_id = overlay['analysis_id']
                jpeg = processor.streams[1].latest_jpeg
                shown = int(cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE).mean().round())
                difference = abs(shown - values[overlay['frame_ts']])
                gaps.append(min(difference, 250 - difference))  # el contador da la vuelta en 250
            time.sleep(0.01)
        processor.unsubscribe(1, viewer)
        processor.stop_monitoring(1)
        gaps = gaps[1:]  # la primera muestra puede llegar mientras el retraso se ajusta
        self.assertGreaterEqual(len(gaps), 3)
        # Sin sincronizar, la diferencia sería ~25 frames (0.5 s a ~50 FPS).
        self.assertLessEqual(max(gaps), 6, f'Frames de diferencia: {gaps}')


class LiveModeTest(unittest.TestCase):
    def test_live_feed_is_not_delayed(self):
        processor = make_processor()
        processor.start_monitoring(1, 'cam1', [GLOVES], lambda *a: None, config=FAST)
        viewer = processor.subscribe(1, 'cam1')
        stream = processor.streams[1]
        with stream.condition:
            stream.sync_delay = 2.0  # como si el análisis tardara 2 s
        time.sleep(1.0)
        live_frame, _, live_sequence = processor.get_frame(1, 0, timeout=1, live=True)
        self.assertIsNotNone(live_frame)
        self.assertGreater(live_sequence, stream.jpeg_sequence, 'En vivo va por delante del video sincronizado')
        processor.unsubscribe(1, viewer)
        processor.stop_monitoring(1)


class WarmUpTest(unittest.TestCase):
    def test_websocket_reports_synchronizing_while_monitoring_without_overlay(self):
        from app.core import database
        from app.modules.ai_vision.models import Camera
        from tools.create_admin import create_admin
        client = TestClient(app)
        client.__enter__()
        create_admin('admin_test', 'admin-pass', 'Admin Pruebas')
        with database.SessionLocal() as db:
            camera = Camera(name='Cam warm', laboratory_type='QUIMICA', stream_url='cam-warm')
            db.add(camera)
            db.commit()
            camera_id = camera.id
        token = create_access_token({'sub': 'admin_test', 'role': 'ADMINISTRADOR'})
        with mock.patch('app.modules.ai_vision.router.video_processor') as processor:
            processor.latest_overlay.return_value = None
            processor.monitoring_status.return_value = {'sync_delay': 2.5, 'analysis_rate': 0.3, 'started_at': 1.0}
            with client.websocket_connect(f'/modules/ai_vision/ws/detections/{camera_id}?token={token}') as socket:
                message = socket.receive_json()
                self.assertTrue(message['monitoring'])
                self.assertTrue(message['warming_up'])
                self.assertEqual(message['sync_delay'], 2.5)
                self.assertEqual(message['analysis_rate'], 0.3)
        client.__exit__(None, None, None)

    def test_status_includes_sync_fields(self):
        processor = make_processor()
        processor.start_monitoring(1, 'cam1', [GLOVES], lambda *a: None, config=FAST)
        status = processor.monitoring_status(1)
        self.assertIn('sync_delay', status)
        self.assertIn('analysis_rate', status)
        processor.stop_monitoring(1)
