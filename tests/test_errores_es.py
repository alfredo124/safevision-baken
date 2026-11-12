"""Los errores que llegan a la pantalla están en español y en texto simple (la web muestra `detail` tal cual)."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import unittest

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token
from app.main import app


class ErroresEnEspanolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        from tools.create_admin import create_admin
        create_admin('admin_test', 'admin-pass', 'Admin Pruebas')
        cls.admin = {'Authorization': f"Bearer {create_access_token({'sub': 'admin_test', 'role': 'ADMINISTRADOR'})}"}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_sin_sesion(self):
        response = self.client.get('/modules/ai_vision/cameras')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['detail'], 'Inicia sesión para continuar')

    def test_ruta_inexistente(self):
        response = self.client.get('/no-existe')
        self.assertEqual(response.json()['detail'], 'No encontrado')

    def test_validacion_es_texto_en_espanol(self):
        response = self.client.post('/modules/ai_vision/cameras/upload', headers=self.admin,
                                    data={'laboratory_type': 'BIOLOGIA'})
        self.assertEqual(response.status_code, 422)
        detail = response.json()['detail']
        self.assertIsInstance(detail, str)
        self.assertIn('Datos no válidos', detail)
        self.assertIn('«name» es obligatorio', detail)
        self.assertIn('«laboratory_type» tiene un formato no válido', detail)

    def test_usuario_repetido(self):
        body = {'username': 'admin_test', 'password': 'otra-clave-123', 'full_name': 'Otro Admin', 'role': 'ADMINISTRADOR'}
        response = self.client.post('/modules/auth_db/users', headers=self.admin, json=body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'Ese nombre de usuario ya está registrado')


if __name__ == '__main__':
    unittest.main()
