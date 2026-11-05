"""Ajuste automático al equipo: el límite por cámara sale del tiempo medido. Pipeline simulado."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import os
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.modules.ai_vision import performance, services
from app.modules.auth_db.models import User, UserRole


class SlowPipeline:
    def __init__(self, seconds):
        self.seconds, self.calls = seconds, 0

    def analyze(self, frame, timestamp, conf):
        self.calls += 1
        time.sleep(self.seconds)


class PerformanceTest(unittest.TestCase):
    def setUp(self):
        performance.reset()
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        os.environ.pop('SAFEVISION_MAX_FPS', None)

    def tearDown(self):
        self.env.stop()
        performance.reset()

    def test_limit_from_measured_time(self):
        self.assertEqual(performance.auto_max_fps_for(555), 2.0, 'PC de 2 núcleos (medido: ~0,55 s): como hasta ahora')
        self.assertEqual(performance.auto_max_fps_for(200), 3.0, 'CPU potente')
        self.assertEqual(performance.auto_max_fps_for(40), 5.0, 'GPU: tope 5')
        self.assertEqual(performance.auto_max_fps_for(None), 2.0, 'Sin medida: valor seguro')

    def test_measures_once_and_drives_the_camera_limit(self):
        self.assertEqual(services.max_processing_fps(), 2.0, 'Antes de medir')
        pipeline = SlowPipeline(0.02)  # ~20 ms por análisis (como una GPU): da 5 aunque el PC esté ocupado
        profile = performance.measure(pipeline)
        self.assertTrue(profile['measured'])
        self.assertEqual(pipeline.calls, 4, 'Calentamiento + 3 medidas')
        self.assertEqual(services.max_processing_fps(), 5.0)
        self.assertEqual(services.VideoStreamProcessor.effective_fps(30), 5.0, 'La cámara usa el límite medido')
        performance.measure(SlowPipeline(1.0))
        self.assertEqual(services.max_processing_fps(), 5.0, 'Solo se mide una vez')

    def test_manual_value_wins(self):
        performance.measure(SlowPipeline(0.05))
        with mock.patch.dict(os.environ, {'SAFEVISION_MAX_FPS': '1.5'}):
            self.assertEqual(services.max_processing_fps(), 1.5)
            self.assertTrue(services.performance_summary()['manual'])

    def test_failed_measure_keeps_default(self):
        class Broken:
            def analyze(self, *args):
                raise RuntimeError('sin modelo')
        self.assertEqual(performance.measure(Broken())['auto_max_fps'], 2.0)

    def test_endpoint_requires_session(self):
        with TestClient(app) as client:
            self.assertEqual(client.get('/modules/ai_vision/rendimiento').status_code, 401)
            assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
            with database.SessionLocal() as db:
                if not db.query(User).filter_by(username='perf_admin').first():
                    db.add(User(username='perf_admin', hashed_password=get_password_hash('x123456'),
                                full_name='perf', role=UserRole.ADMINISTRADOR))
                    db.commit()
            token = create_access_token({'sub': 'perf_admin', 'role': 'ADMINISTRADOR'})
            body = client.get('/modules/ai_vision/rendimiento', headers={'Authorization': f'Bearer {token}'}).json()
            self.assertEqual(body['max_fps'], 2.0)
            self.assertFalse(body['measured'])


if __name__ == '__main__':
    unittest.main()
