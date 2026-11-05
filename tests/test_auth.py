"""Permisos de los endpoints de usuarios (Tarea 0). Usa una SQLite temporal."""
import os
import unittest

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.modules.auth_db.models import User, UserRole
from tools.create_admin import create_admin

USERS = '/modules/auth_db/users'
NEW_USER = {
    'username': 'nuevo',
    'password': 'clave-segura',
    'full_name': 'Nuevo Usuario',
    'role': 'ADMINISTRADOR',
}


def _token(username: str, role: UserRole) -> dict:
    return {'Authorization': f"Bearer {create_access_token({'sub': username, 'role': role.value})}"}


class AuthPermissionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert database.SQLALCHEMY_DATABASE_URL == os.environ['LOCAL_DATABASE_URL']
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()  # ejecuta el arranque (creación de tablas)
        create_admin('admin_test', 'admin-pass', 'Admin Pruebas')
        with database.SessionLocal() as db:
            db.add(User(
                username='encargado_test',
                hashed_password=get_password_hash('pass123'),
                full_name='Encargado Química',
                role=UserRole.ENCARGADO_QUIMICA,
            ))
            db.commit()
        cls.admin = _token('admin_test', UserRole.ADMINISTRADOR)
        cls.assistant = _token('encargado_test', UserRole.ENCARGADO_QUIMICA)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_anonymous_cannot_create_user(self):
        self.assertEqual(self.client.post(USERS, json=NEW_USER).status_code, 401)

    def test_assistant_cannot_create_user(self):
        response = self.client.post(USERS, json={**NEW_USER, 'username': 'x1'}, headers=self.assistant)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_user(self):
        response = self.client.post(
            USERS, json={**NEW_USER, 'username': 'creado_por_admin', 'role': 'ENCARGADO_FISICA'},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['role'], 'ENCARGADO_FISICA')
        self.assertNotIn('password', response.json())
        self.assertNotIn('hashed_password', response.json())

    def test_patch_requires_admin(self):
        url = f'{USERS}/encargado_test'
        self.assertEqual(self.client.patch(url, json={'phone': '1'}).status_code, 401)
        self.assertEqual(self.client.patch(url, json={'phone': '1'}, headers=self.assistant).status_code, 403)
        response = self.client.patch(url, json={'phone': '999'}, headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['phone'], '999')
        self.assertEqual(response.json()['full_name'], 'Encargado Química')  # no se borra lo no enviado

    def test_me_requires_session_and_ignores_username_param(self):
        self.assertEqual(self.client.get('/modules/auth_db/me?username=admin_test').status_code, 401)
        response = self.client.get('/modules/auth_db/me?username=admin_test', headers=self.assistant)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['username'], 'encargado_test')

    def test_invalid_token_is_rejected(self):
        response = self.client.post(USERS, json=NEW_USER, headers={'Authorization': 'Bearer basura'})
        self.assertEqual(response.status_code, 401)

    def test_login_still_works(self):
        response = self.client.post('/modules/auth_db/login', json={'username': 'admin_test', 'password': 'admin-pass'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('access_token', response.json())

    def test_create_admin_script_does_not_overwrite(self):
        self.assertFalse(create_admin('admin_test', 'otra-clave', 'Otro'))
        response = self.client.post('/modules/auth_db/login', json={'username': 'admin_test', 'password': 'admin-pass'})
        self.assertEqual(response.status_code, 200)


if __name__ == '__main__':
    unittest.main()
