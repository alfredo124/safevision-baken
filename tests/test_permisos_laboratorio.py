"""Cada encargado ve solo su laboratorio asignado; el administrador general ve todo (2026-11-11)."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import unittest

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.modules.ai_vision.models import Camera
from app.modules.auth_db.models import User, UserRole
from app.modules.reglas_lab import models
from app.modules.reglas_lab.cumplimiento.motor import ViolationEvent
from app.modules.reglas_lab.incidencias.servicio import record_system_fault
from tests.ayuda import encargado_con_laboratorio

CAMS = '/modules/ai_vision/cameras'
LAB = '/modules/reglas_lab'


def headers(username, role):
    return {'Authorization': f"Bearer {create_access_token({'sub': username, 'role': role})}"}


class PermisosPorLaboratorioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        from tools.create_admin import create_admin
        create_admin('admin_permisos', 'admin-pass', 'Admin Permisos')
        with database.SessionLocal() as db:
            # Dos laboratorios de Química (por ejemplo, en dos locales) con una cámara cada uno.
            cam_a = Camera(name='Mesa A', laboratory_type='QUIMICA', stream_url='perm-a')
            cam_b = Camera(name='Mesa B', laboratory_type='QUIMICA', stream_url='perm-b')
            db.add_all([cam_a, cam_b])
            db.commit()
            cls.cam_a, cls.cam_b = cam_a.id, cam_b.id
            cls.lab_a = encargado_con_laboratorio(db, 'perm_a', 'QUIMICA', camera_ids=[cam_a.id])
            cls.lab_b = encargado_con_laboratorio(db, 'perm_b', 'QUIMICA', camera_ids=[cam_b.id])
            db.add(User(username='perm_sin_lab', hashed_password=get_password_hash('x123456'),
                        full_name='Sin laboratorio', role=UserRole.ENCARGADO_QUIMICA))
            db.commit()
            event = ViolationEvent(1, 'guantes', 'gloves', 'ALTO', 0.0, 0.0, (0, 0, 10, 10), 0.0, rule_code='RLQ-2.3')
            cls.incidente_b = record_system_fault(db, cam_b.id, cls.lab_b, event, (640, 480)).id
        cls.admin = headers('admin_permisos', 'ADMINISTRADOR')
        cls.a = headers('perm_a', 'ENCARGADO_QUIMICA')
        cls.b = headers('perm_b', 'ENCARGADO_QUIMICA')
        cls.sin_lab = headers('perm_sin_lab', 'ENCARGADO_QUIMICA')

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _ids(self, path, who, key=None):
        data = self.client.get(path, headers=who).json()
        return {item['id'] for item in (data[key] if key else data)}

    def test_misma_area_otro_laboratorio_no_se_ve(self):
        self.assertIn(self.cam_a, self._ids(CAMS, self.a))
        self.assertNotIn(self.cam_b, self._ids(CAMS, self.a))
        self.assertEqual(self.client.get(f'/modules/ai_vision/snapshot/{self.cam_b}', headers=self.a).status_code, 403)
        self.assertEqual(self._ids(f'{LAB}/configuration', self.a, 'workshops'), {self.lab_a})
        self.assertEqual(self.client.get(f'{LAB}/workshops/{self.lab_b}/monitoring', headers=self.a).status_code, 403)

    def test_incidencias_solo_de_su_laboratorio(self):
        self.assertIn(self.incidente_b, self._ids(f'{LAB}/incidents', self.b))
        self.assertNotIn(self.incidente_b, self._ids(f'{LAB}/incidents', self.a))
        self.assertIn(self.incidente_b, self._ids(f'{LAB}/incidents', self.admin))
        review = self.client.post(f'{LAB}/incidents/{self.incidente_b}/review', headers=self.a,
                                  json={'decision': 'DESCARTADA', 'reason': 'x'})
        self.assertEqual(review.status_code, 403)

    def test_administrador_ve_todo(self):
        self.assertTrue({self.cam_a, self.cam_b} <= self._ids(CAMS, self.admin))
        self.assertTrue({self.lab_a, self.lab_b} <= self._ids(f'{LAB}/configuration', self.admin, 'workshops'))

    def test_sin_laboratorio_no_ve_nada(self):
        self.assertEqual(self._ids(CAMS, self.sin_lab), set())
        self.assertEqual(self._ids(f'{LAB}/configuration', self.sin_lab, 'workshops'), set())
        self.assertEqual(self._ids(f'{LAB}/incidents', self.sin_lab), set())
        response = self.client.post(f'{CAMS}/browser', headers=self.sin_lab, json={'name': 'Cel', 'laboratory_type': 'QUIMICA'})
        self.assertEqual(response.status_code, 403)
        self.assertIn('laboratorio asignado', response.json()['detail'])

    def test_camara_nueva_queda_en_su_laboratorio(self):
        response = self.client.post(f'{CAMS}/browser', headers=self.a, json={'name': 'Celular A', 'laboratory_type': 'QUIMICA'})
        self.assertEqual(response.status_code, 201, response.text)
        nueva = response.json()['id']
        self.assertIn(nueva, self._ids(CAMS, self.a))
        self.assertNotIn(nueva, self._ids(CAMS, self.b))
        with database.SessionLocal() as db:
            self.assertEqual(db.query(models.WorkshopCamera).filter_by(camera_id=nueva).first().workshop_id, self.lab_a)

    def test_encargado_no_crea_laboratorios_ni_toma_camaras_ajenas(self):
        crear = self.client.post(f'{LAB}/workshops', headers=self.a, json={'name': 'Otro', 'laboratory_type': 'QUIMICA'})
        self.assertEqual(crear.status_code, 403)
        tomar = self.client.put(f'{LAB}/workshops/{self.lab_a}/configuration', headers=self.a,
                                json={'camera_ids': [self.cam_a, self.cam_b], 'required_equipment_ids': []})
        self.assertEqual(tomar.status_code, 403)
        admin_crea = self.client.post(f'{LAB}/workshops', headers=self.admin, json={'name': 'Química permisos', 'laboratory_type': 'QUIMICA'})
        self.assertEqual(admin_crea.status_code, 201, admin_crea.text)


if __name__ == '__main__':
    unittest.main()
