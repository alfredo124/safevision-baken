"""Avisos de cambios: al agregar o quitar una cámara, todas las pantallas se enteran. SQLite temporal."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import unittest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core import database, events
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.modules.auth_db.models import User, UserRole


class EventsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        with database.SessionLocal() as db:
            if not db.query(User).filter_by(username='admin_eventos').first():
                db.add(User(username='admin_eventos', hashed_password=get_password_hash('x123456'),
                            full_name='Admin eventos', role=UserRole.ADMINISTRADOR))
                db.commit()
        cls.token = create_access_token({'sub': 'admin_eventos', 'role': 'ADMINISTRADOR'})
        cls.headers = {'Authorization': f'Bearer {cls.token}'}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_rules(self):
        self.assertEqual(events.topics_for_request('POST', '/modules/ai_vision/cameras/browser', 201), ('cameras',))
        self.assertEqual(events.topics_for_request('DELETE', '/modules/ai_vision/cameras/4', 204), ('cameras',))
        self.assertEqual(events.topics_for_request('PUT', '/modules/reglas_lab/workshops/2/configuration', 200),
                         ('workshops', 'cameras'))
        self.assertEqual(events.topics_for_request('POST', '/modules/reglas_lab/workshops/2/monitoring/start', 200),
                         ('practica', 'incidents'))
        self.assertEqual(events.topics_for_request('POST', '/modules/reglas_lab/incidents/7/review', 200), ('incidents',))
        self.assertEqual(events.topics_for_request('GET', '/modules/ai_vision/cameras', 200), (), 'Leer no avisa')
        self.assertEqual(events.topics_for_request('POST', '/modules/ai_vision/cameras', 403), (), 'Un error no avisa')

    def test_screens_hear_about_a_new_camera(self):
        with self.client.websocket_connect(f'/ws/events?token={self.token}') as socket:
            self.assertEqual(socket.receive_json()['type'], 'hello')
            response = self.client.post('/modules/ai_vision/cameras/browser', headers=self.headers,
                                        json={'name': 'Celular eventos', 'laboratory_type': 'QUIMICA'})
            self.assertEqual(response.status_code, 201, response.text)
            message = socket.receive_json()
            self.assertEqual(message['type'], 'changed')
            self.assertIn('cameras', message['topics'])

    def test_requires_session(self):
        with self.assertRaises(WebSocketDisconnect) as closed:
            with self.client.websocket_connect('/ws/events?token=basura') as socket:
                socket.receive_json()
        self.assertEqual(closed.exception.code, 4401)


if __name__ == '__main__':
    unittest.main()
