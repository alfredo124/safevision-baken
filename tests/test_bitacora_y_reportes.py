"""Datos que antes no quedaban guardados:

- Bitácora: inicios de sesión, validaciones, prácticas y reportes quedan registrados con usuario e IP.
- Reglas por práctica: cada práctica guarda las reglas de EPP exigidas, y el reporte usa esa copia
  aunque después cambie la configuración del laboratorio.
- Reportes emitidos: quedan guardados con su huella y se pueden verificar.
- Historial: filtra por periodo con zona horaria en el servidor.
"""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token
from app.main import app
from app.modules.ai_vision.models import Camera
from app.modules.auth_db.models import ActivityLog
from app.modules.reglas_lab import models
from app.modules.reglas_lab.compliance import ViolationEvent
from app.modules.reglas_lab.incident_service import record_system_fault
from tests.ayuda import encargado_con_laboratorio

LAB = '/modules/reglas_lab'
AUTH = '/modules/auth_db'


def headers(username, role):
    return {'Authorization': f"Bearer {create_access_token({'sub': username, 'role': role})}"}


def evento(equipment='guantes', track=1):
    return ViolationEvent(track, equipment, 'gloves', 'ALTO', 0.0, 0.0, (0, 0, 10, 10), 0.0, rule_code='R-1')


def periodo(horas=2):
    """Periodo de las últimas `horas`, con zona (como lo envía la web)."""
    ahora = datetime.now(timezone.utc)
    return {'start': (ahora - timedelta(hours=horas)).isoformat(), 'end': (ahora + timedelta(minutes=1)).isoformat()}


class BitacoraYReportesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        from tools.create_admin import create_admin
        create_admin('admin_bitacora', 'admin-pass', 'Admin Bitácora')
        with database.SessionLocal() as db:
            cam = Camera(name='Mesa Bit', laboratory_type='QUIMICA', stream_url='bit-a')
            cam_otro = Camera(name='Mesa Bit Otro', laboratory_type='FISICA', stream_url='bit-b')
            db.add_all([cam, cam_otro])
            db.commit()
            cls.cam = cam.id
            cls.lab = encargado_con_laboratorio(db, 'bit_a', 'QUIMICA', full_name='Encargada Bit', camera_ids=[cam.id])
            cls.lab_otro = encargado_con_laboratorio(db, 'bit_b', 'FISICA', camera_ids=[cam_otro.id])
            db.add(models.WorkshopEquipmentRule(workshop_id=cls.lab, equipment_id='guantes', is_required=True,
                                                rule_code='RLQ-2.3'))
            db.commit()
        cls.admin = headers('admin_bitacora', 'ADMINISTRADOR')
        cls.a = headers('bit_a', 'ENCARGADO_QUIMICA')
        cls.b = headers('bit_b', 'ENCARGADO_FISICA')

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _post(self, accion, who=None):
        response = self.client.post(f'{LAB}/workshops/{self.lab}/monitoring/{accion}', headers=who or self.a)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _configurar(self, equipment_ids):
        response = self.client.put(f'{LAB}/workshops/{self.lab}/configuration', headers=self.a, json={
            'camera_ids': [self.cam],
            'required_equipment_ids': equipment_ids,
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _bitacora(self, **params):
        response = self.client.get(f'{AUTH}/activity', params=params, headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # ------------------------------------------------------------------ bitácora
    def test_inicio_de_sesion_queda_en_la_bitacora(self):
        ok = self.client.post(f'{AUTH}/login', json={'username': 'admin_bitacora', 'password': 'admin-pass'})
        self.assertEqual(ok.status_code, 200)
        mal = self.client.post(f'{AUTH}/login', json={'username': 'admin_bitacora', 'password': 'otra'})
        self.assertEqual(mal.status_code, 401)
        filas = self._bitacora(module='usuarios')
        acciones = [(f['action'], f['user_name']) for f in filas[:2]]
        self.assertEqual(acciones, [('INICIO_SESION_FALLIDO', 'admin_bitacora'), ('INICIAR_SESION', 'Admin Bitácora')])
        self.assertIsNotNone(filas[0]['ip_address'], 'La IP llega desde el middleware')
        self.assertNotIn('otra', filas[0]['description'] or '', 'Nunca se guarda la contraseña')

    def test_solo_el_administrador_ve_la_bitacora(self):
        self.assertEqual(self.client.get(f'{AUTH}/activity', headers=self.a).status_code, 403)
        self.assertEqual(self.client.get(f'{AUTH}/activity', params={'module': 'otro'}, headers=self.admin).status_code, 422)

    def test_validar_una_alerta_queda_en_la_bitacora(self):
        with database.SessionLocal() as db:
            incidencia = record_system_fault(db, self.cam, self.lab, evento(track=50), (640, 480))
            codigo, incident_id = incidencia.code, incidencia.id
        respuesta = self.client.post(f'{LAB}/incidents/{incident_id}/review', headers=self.a,
                                     json={'decision': 'DESCARTADA', 'reason': 'Guantes transparentes'})
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        fila = self._bitacora(module='incidencias')[0]
        self.assertEqual(fila['action'], 'DESCARTAR_ALERTA')
        self.assertEqual(fila['user_name'], 'Encargada Bit')
        self.assertIn(codigo, fila['description'])
        self.assertIn('Guantes transparentes', fila['description'])

    # ------------------------------------------------------- reglas por práctica
    def test_la_practica_guarda_las_reglas_y_el_reporte_las_usa(self):
        self._configurar(['guantes'])
        sesion = self._post('start')['session']
        # Cambio con la práctica en curso: se añade una versión nueva.
        self._configurar(['guantes', 'lentes'])
        self._post('stop')
        with database.SessionLocal() as db:
            copia = db.get(models.PracticeSession, sesion['id']).rules_snapshot
        self.assertEqual([[r['equipment_id'] for r in v['rules']] for v in copia], [['guantes'], ['guantes', 'lentes']])
        self.assertEqual(copia[0]['rules'][0]['rule_code'], 'RLQ-2.3')

        # Después de la práctica se quita todo el EPP: el reporte sigue mostrando lo que se exigió.
        self._configurar([])
        reporte = self.client.get(f'{LAB}/report', params=periodo(), headers=self.a).json()
        lab = next(w for w in reporte['workshops'] if w['workshop_id'] == self.lab)
        self.assertEqual(lab['required_equipment_source'], 'PRACTICAS')
        self.assertEqual(set(lab['required_equipment']), {'Guantes Industriales', 'Lentes de Protección'})

        acciones = [f['action'] for f in self._bitacora(module='practicas')[:2]]
        self.assertEqual(acciones, ['FINALIZAR_PRACTICA', 'INICIAR_PRACTICA'])

    # ---------------------------------------------------------- reportes emitidos
    def test_emitir_y_verificar_un_reporte(self):
        with database.SessionLocal() as db:
            incident_id = record_system_fault(db, self.cam, self.lab, evento(track=60), (640, 480)).id
        datos = {**periodo(), 'workshop_id': self.lab, 'responsible': 'Comité SST'}
        emitido = self.client.post(f'{LAB}/reports', json=datos, headers=self.a)
        self.assertEqual(emitido.status_code, 201, emitido.text)
        emitido = emitido.json()
        self.assertTrue(emitido['issued'])
        self.assertEqual(emitido['responsible'], 'Comité SST')

        lista = self.client.get(f'{LAB}/reports', headers=self.a).json()
        self.assertEqual(lista[0]['code'], emitido['report_id'])
        self.assertEqual(lista[0]['fingerprint'], emitido['fingerprint'])
        # Otro encargado no ve reportes de un laboratorio ajeno.
        self.assertNotIn(emitido['report_id'], [r['code'] for r in self.client.get(f'{LAB}/reports', headers=self.b).json()])
        self.assertEqual(self.client.get(f"{LAB}/reports/{emitido['report_id']}/verify", headers=self.b).status_code, 404)

        verificado = self.client.get(f"{LAB}/reports/{emitido['report_id']}/verify", headers=self.a).json()
        self.assertTrue(verificado['matches'], 'Sin cambios, la huella coincide')

        # Se valida una alerta del periodo: el reporte emitido ya no corresponde a los datos.
        self.client.post(f'{LAB}/incidents/{incident_id}/review', headers=self.a,
                         json={'decision': 'CONFIRMADA', 'action': 'CORREGIDO'})
        verificado = self.client.get(f"{LAB}/reports/{emitido['report_id']}/verify", headers=self.a).json()
        self.assertFalse(verificado['matches'])
        self.assertEqual(self._bitacora(module='reportes')[0]['action'], 'EMITIR_REPORTE')

    def test_reporte_de_todos_guarda_la_lista_de_laboratorios(self):
        emitido = self.client.post(f'{LAB}/reports', json={**periodo(), 'responsible': ''}, headers=self.admin).json()
        with database.SessionLocal() as db:
            fila = db.query(models.IssuedReport).filter_by(code=emitido['report_id']).one()
            self.assertIn(self.lab, fila.workshop_ids)
            self.assertIn(self.lab_otro, fila.workshop_ids)
            self.assertIsNone(fila.responsible)
        # El encargado no ve un reporte que incluye otros laboratorios.
        self.assertNotIn(emitido['report_id'], [r['code'] for r in self.client.get(f'{LAB}/reports', headers=self.a).json()])

    # ------------------------------------------------------------------ historial
    def test_historial_filtra_por_periodo_en_el_servidor(self):
        with database.SessionLocal() as db:
            reciente = record_system_fault(db, self.cam, self.lab, evento(track=70), (640, 480))
            antigua = models.Incident(code='INC-ANTIGUA-BIT', workshop_id=self.lab, camera_id=self.cam, title='x',
                                      description='x', severity='ALTO',
                                      occurred_at=datetime.now(timezone.utc) - timedelta(days=40))
            db.add(antigua)
            db.commit()
            ids = reciente.id, antigua.id
        hoy = self.client.get(f'{LAB}/incidents', params=periodo(), headers=self.a).json()
        self.assertIn(ids[0], [i['id'] for i in hoy])
        self.assertNotIn(ids[1], [i['id'] for i in hoy])
        hace_40 = datetime.now(timezone.utc) - timedelta(days=40)
        antes = self.client.get(f'{LAB}/incidents', headers=self.a, params={
            'start': (hace_40 - timedelta(days=1)).isoformat(), 'end': (hace_40 + timedelta(days=1)).isoformat()}).json()
        self.assertEqual([i['id'] for i in antes], [ids[1]])
        self.assertEqual(len(self.client.get(f'{LAB}/incidents', params={'limit': 1}, headers=self.a).json()), 1)


if __name__ == '__main__':
    unittest.main()
