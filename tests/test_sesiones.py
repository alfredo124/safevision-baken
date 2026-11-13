"""Sesiones de práctica: cada práctica queda guardada con su laboratorio y las incidencias ocurridas en ella."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token
from app.main import app
from app.modules.ai_vision.models import Camera
from app.modules.reglas_lab import models
from app.modules.reglas_lab.practicas import sesiones
from app.modules.reglas_lab.cumplimiento.motor import ViolationEvent
from app.modules.reglas_lab.incidencias.servicio import record_system_fault
from tests.ayuda import encargado_con_laboratorio

LAB = '/modules/reglas_lab'


def headers(username, role):
    return {'Authorization': f"Bearer {create_access_token({'sub': username, 'role': role})}"}


def evento(equipment='guantes', track=1):
    return ViolationEvent(track, equipment, 'gloves', 'ALTO', 0.0, 0.0, (0, 0, 10, 10), 0.0, rule_code='R-1')


class SesionesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        from tools.create_admin import create_admin
        create_admin('admin_sesiones', 'admin-pass', 'Admin Sesiones')
        with database.SessionLocal() as db:
            campus = models.Campus(name='Local Sesiones')
            cam = Camera(name='Mesa Ses', laboratory_type='QUIMICA', stream_url='ses-a')
            cam_otro = Camera(name='Mesa Ses Otro', laboratory_type='FISICA', stream_url='ses-b')
            db.add_all([campus, cam, cam_otro])
            db.commit()
            cls.cam = cam.id
            cls.lab = encargado_con_laboratorio(db, 'ses_a', 'QUIMICA', full_name='Encargada A', camera_ids=[cam.id])
            cls.lab_otro = encargado_con_laboratorio(db, 'ses_b', 'FISICA', camera_ids=[cam_otro.id])
            db.get(models.Workshop, cls.lab).campus_id = campus.id
            db.commit()
        cls.admin = headers('admin_sesiones', 'ADMINISTRADOR')
        cls.a = headers('ses_a', 'ENCARGADO_QUIMICA')
        cls.b = headers('ses_b', 'ENCARGADO_FISICA')

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _post(self, accion, who=None, lab=None):
        response = self.client.post(f'{LAB}/workshops/{lab or self.lab}/monitoring/{accion}', headers=who or self.a)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _falta(self, equipment='guantes', track=1):
        with database.SessionLocal() as db:
            return record_system_fault(db, self.cam, self.lab, evento(equipment, track), (640, 480))

    def test_ciclo_de_una_practica(self):
        inicio = self._post('start')['session']
        self.assertTrue(inicio['active'])
        self.assertEqual(inicio['started_by_name'], 'Encargada A')
        self.assertEqual(inicio['campus_name'], 'Local Sesiones')
        # Iniciar otra vez (otro equipo, recarga) no abre una sesión nueva.
        self.assertEqual(self._post('start')['session']['id'], inicio['id'])
        estado = self.client.get(f'{LAB}/workshops/{self.lab}/monitoring', headers=self.a).json()
        self.assertEqual(estado['session']['id'], inicio['id'])

        incidencia = self._falta()
        self.assertEqual(incidencia.session_id, inicio['id'])
        por_sesion = self.client.get(f'{LAB}/incidents', params={'session_id': inicio['id']}, headers=self.a).json()
        self.assertEqual([i['id'] for i in por_sesion], [incidencia.id])
        self.assertEqual(por_sesion[0]['session_code'], inicio['code'])

        fin = self._post('stop')['session']
        self.assertFalse(fin['active'])
        self.assertEqual(fin['end_reason'], 'FINALIZADA')
        self.assertEqual(fin['alerts'], 1)
        self.assertIsNone(self.client.get(f'{LAB}/workshops/{self.lab}/monitoring', headers=self.a).json()['session'])

        # La siguiente práctica es otra sesión y no agrupa con la alerta pendiente de la anterior.
        nueva = self._post('start')['session']
        self.assertNotEqual(nueva['id'], inicio['id'])
        otra = self._falta('lentes')
        self.assertNotEqual(otra.id, incidencia.id)
        self.assertEqual(otra.session_id, nueva['id'])
        self._post('stop')

    def test_panel_solo_muestra_alertas_de_la_practica_en_curso(self):
        """Al terminar la práctica sus alertas salen del panel, pero siguen en el historial."""
        panel = {'status': 'PENDIENTE', 'active_session': True}
        ids = lambda params: {i['id'] for i in self.client.get(f'{LAB}/incidents', params=params, headers=self.a).json()}
        primera = self._post('start')['session']
        anterior = self._falta('guantes', track=31)
        self.assertIn(anterior.id, ids(panel))
        self._post('stop')
        self.assertNotIn(anterior.id, ids(panel))
        self.assertIn(anterior.id, ids({'session_id': primera['id']}))  # historial con su práctica
        self.assertIn(anterior.id, ids({'status': 'PENDIENTE'}))  # sigue sin validar
        self._post('start')
        nueva = self._falta('lentes', track=32)
        self.assertEqual(ids(panel) & {anterior.id, nueva.id}, {nueva.id})
        self._post('stop')

    def test_sin_practica_la_incidencia_no_tiene_sesion(self):
        self.assertIsNone(self._falta('chaleco', track=7).session_id)

    def test_servidor_reiniciado_cierra_como_interrumpida(self):
        with database.SessionLocal() as db:
            workshop = db.get(models.Workshop, self.lab_otro)
            sesion = sesiones.start_session(db, workshop, None)
            sesion.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
            ultima = datetime.now(timezone.utc) - timedelta(hours=1)
            db.add(models.Incident(code='INC-SES-INT', workshop_id=self.lab_otro, camera_id=self.cam, title='t',
                                   description='d', severity='ALTO', occurred_at=ultima, session_id=sesion.id))
            db.commit()
            self.assertGreaterEqual(sesiones.close_interrupted_sessions(db), 1)
            db.refresh(sesion)
            self.assertEqual(sesion.end_reason, 'INTERRUMPIDA')
            self.assertAlmostEqual(sesiones._utc(sesion.ended_at).timestamp(), ultima.timestamp(), delta=1)

    def test_lista_de_sesiones_por_permiso(self):
        self._post('start')
        self._post('stop')
        mias = self.client.get(f'{LAB}/sessions', headers=self.a).json()
        self.assertTrue(mias)
        self.assertEqual({s['workshop_id'] for s in mias}, {self.lab})
        self.assertNotIn(self.lab, {s['workshop_id'] for s in self.client.get(f'{LAB}/sessions', headers=self.b).json()})
        self.assertIn(self.lab, {s['workshop_id'] for s in self.client.get(f'{LAB}/sessions', headers=self.admin).json()})

    def test_reporte_cuenta_sesiones_y_horas(self):
        with database.SessionLocal() as db:
            workshop = db.get(models.Workshop, self.lab)
            sesion = sesiones.start_session(db, workshop, None)
            sesion.started_at = datetime(2025, 5, 10, 14, 0, tzinfo=timezone.utc)
            sesion.ended_at = datetime(2025, 5, 10, 15, 30, tzinfo=timezone.utc)
            sesion.end_reason = 'FINALIZADA'
            db.commit()
            sesion_id, sesion_code = sesion.id, sesion.code
        params = {'start': '2025-05-10T00:00:00+00:00', 'end': '2025-05-11T00:00:00+00:00', 'workshop_id': self.lab}
        data = self.client.get(f'{LAB}/report', params=params, headers=self.admin).json()
        self.assertEqual(data['summary']['sessions'], 1)
        self.assertEqual(data['summary']['supervised_seconds'], 90 * 60)
        self.assertEqual(data['sessions'][0]['supervised_seconds'], 90 * 60)
        self.assertEqual(data['workshops'][0]['sessions'], 1)
        # Cada incidencia indica su práctica, para agruparlas en el documento.
        with database.SessionLocal() as db:
            db.add(models.Incident(code='INC-SES-REP', workshop_id=self.lab, camera_id=self.cam, title='t',
                                   description='d', severity='ALTO', session_id=sesion_id,
                                   occurred_at=datetime(2025, 5, 10, 14, 30, tzinfo=timezone.utc)))
            db.commit()
        fila = next(i for i in self.client.get(f'{LAB}/report', params=params, headers=self.admin).json()['incidents']
                    if i['code'] == 'INC-SES-REP')
        self.assertEqual((fila['session_id'], fila['session_code']), (sesion_id, sesion_code))
        # Solo cuenta el tiempo dentro del periodo pedido.
        recorte = {**params, 'start': '2025-05-10T15:00:00+00:00'}
        self.assertEqual(self.client.get(f'{LAB}/report', params=recorte, headers=self.admin)
                         .json()['summary']['supervised_seconds'], 30 * 60)


if __name__ == '__main__':
    unittest.main()
