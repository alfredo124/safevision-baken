"""Configuración inicial, gestión de usuarios y locales (solo el administrador general)."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import unittest

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token
from app.main import app
from app.modules.auth_db.models import User, UserRole
from app.modules.reglas_lab.models import Campus, Workshop

AUTH = '/modules/auth_db'
LAB = '/modules/reglas_lab'


class UsuariosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        from tools.create_admin import create_admin
        create_admin('admin_usuarios', 'admin-pass', 'Admin Usuarios')
        cls.admin = {'Authorization': f"Bearer {create_access_token({'sub': 'admin_usuarios', 'role': 'ADMINISTRADOR'})}"}
        with database.SessionLocal() as db:
            campus = Campus(name='Local de pruebas usuarios', city='Arequipa')
            db.add(campus)
            db.flush()
            quimica = Workshop(name='Química usuarios', laboratory_type='QUIMICA', campus_id=campus.id)
            db.add(quimica)
            db.commit()
            cls.campus_id, cls.quimica_id = campus.id, quimica.id

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _crear(self, username, **extra):
        body = {'username': username, 'password': 'clave-segura', 'full_name': 'Ana Torres', 'dni': '12345678',
                'email': 'ana@utp.edu.pe', 'role': 'ENCARGADO_QUIMICA', 'workshop_id': self.quimica_id, **extra}
        return self.client.post(f'{AUTH}/users', headers=self.admin, json=body)

    def test_encargado_hereda_laboratorio_y_local(self):
        response = self._crear('ana_torres')
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual((data['workshop_name'], data['campus_name'], data['role']),
                         ('Química usuarios', 'Local de pruebas usuarios', 'ENCARGADO_QUIMICA'))
        login = self.client.post(f'{AUTH}/login', json={'username': 'ana_torres', 'password': 'clave-segura'})
        self.assertEqual(login.json()['user']['campus_name'], 'Local de pruebas usuarios')
        listado = self.client.get(f'{AUTH}/users', headers=self.admin).json()
        self.assertIn('ana_torres', [u['username'] for u in listado])

    def test_encargado_sin_laboratorio_no_se_crea(self):
        response = self._crear('sin_lab', workshop_id=None)
        self.assertEqual(response.json()['detail'], 'Asigna un laboratorio al encargado')

    def test_datos_invalidos(self):
        self.assertEqual(self._crear('dni_malo', dni='123').status_code, 422)
        self.assertEqual(self._crear('clave_corta', password='123').status_code, 422)

    def test_desactivar_impide_entrar_y_restablecer_clave(self):
        self._crear('pedro')
        url = f'{AUTH}/users/pedro'
        self.client.patch(url, headers=self.admin, json={'password': 'clave-nueva-1'})
        ok = self.client.post(f'{AUTH}/login', json={'username': 'pedro', 'password': 'clave-nueva-1'})
        self.assertEqual(ok.status_code, 200)
        self.client.patch(url, headers=self.admin, json={'is_active': False})
        bloqueado = self.client.post(f'{AUTH}/login', json={'username': 'pedro', 'password': 'clave-nueva-1'})
        self.assertEqual(bloqueado.status_code, 401)
        self.assertIn('desactivado', bloqueado.json()['detail'])

    def test_admin_no_puede_quitarse_el_rol(self):
        response = self.client.patch(f'{AUTH}/users/admin_usuarios', headers=self.admin, json={'is_active': False})
        self.assertEqual(response.status_code, 400)

    def test_locales_solo_admin(self):
        response = self.client.post(f'{LAB}/campuses', headers=self.admin, json={'name': 'Cerro Colorado', 'city': 'Arequipa'})
        self.assertEqual(response.status_code, 201, response.text)
        duplicado = self.client.post(f'{LAB}/campuses', headers=self.admin, json={'name': 'Cerro Colorado'})
        self.assertEqual(duplicado.status_code, 409)
        self._crear('encargado_local')
        encargado = {'Authorization': f"Bearer {create_access_token({'sub': 'encargado_local', 'role': 'ENCARGADO_QUIMICA'})}"}
        self.assertEqual(self.client.post(f'{LAB}/campuses', headers=encargado, json={'name': 'X local'}).status_code, 403)
        self.assertEqual(self.client.get(f'{LAB}/campuses', headers=encargado).status_code, 200)

    def test_laboratorio_cambia_de_local(self):
        nuevo = self.client.post(f'{LAB}/campuses', headers=self.admin, json={'name': 'Local destino'}).json()
        response = self.client.patch(f'{LAB}/workshops/{self.quimica_id}', headers=self.admin,
                                     json={'campus_id': nuevo['id']})
        self.assertEqual(response.json()['campus_name'], 'Local destino')
        self.client.patch(f'{LAB}/workshops/{self.quimica_id}', headers=self.admin, json={'campus_id': self.campus_id})

    def test_configuracion_inicial(self):
        """Solo funciona mientras no hay administrador activo; después queda cerrada."""
        with database.SessionLocal() as db:
            admins = [u.id for u in db.query(User).filter(User.role == UserRole.ADMINISTRADOR, User.is_active.isnot(False))]
            db.query(User).filter(User.id.in_(admins)).update({'is_active': False}, synchronize_session=False)
            db.commit()
        try:
            self.assertTrue(self.client.get(f'{AUTH}/setup').json()['needs_setup'])
            body = {'username': 'admin_general', 'password': 'clave-segura', 'full_name': 'Admin General'}
            creado = self.client.post(f'{AUTH}/setup', json=body)
            self.assertEqual(creado.status_code, 201, creado.text)
            self.assertEqual(creado.json()['user']['role'], 'ADMINISTRADOR')
            self.assertIn('access_token', creado.json())
            self.assertFalse(self.client.get(f'{AUTH}/setup').json()['needs_setup'])
            otra_vez = self.client.post(f'{AUTH}/setup', json={**body, 'username': 'intruso'})
            self.assertEqual(otra_vez.status_code, 409)
        finally:
            with database.SessionLocal() as db:
                db.query(User).filter(User.username == 'admin_general').delete()
                db.query(User).filter(User.id.in_(admins)).update({'is_active': True}, synchronize_session=False)
                db.commit()


if __name__ == '__main__':
    unittest.main()
