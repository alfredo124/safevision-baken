"""Reporte SST con datos reales: conteos, periodo, permisos por laboratorio y huella del contenido."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token
from app.main import app
from app.modules.ai_vision.models import Camera
from app.modules.reglas_lab import models
from tests.ayuda import encargado_con_laboratorio

URL = '/modules/reglas_lab/report'
DIA = datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc)


def headers(username, role):
    return {'Authorization': f"Bearer {create_access_token({'sub': username, 'role': role})}"}


def periodo(inicio=DIA - timedelta(days=1), fin=DIA + timedelta(days=1), **extra):
    return {'start': inicio.isoformat(), 'end': fin.isoformat(), **extra}


class ReporteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        from tools.create_admin import create_admin
        create_admin('admin_reporte', 'admin-pass', 'Admin Reporte')
        with database.SessionLocal() as db:
            campus = models.Campus(name='Local Reporte')
            cam_a = Camera(name='Mesa Rep A', laboratory_type='QUIMICA', stream_url='rep-a')
            cam_b = Camera(name='Mesa Rep B', laboratory_type='FISICA', stream_url='rep-b')
            db.add_all([campus, cam_a, cam_b])
            db.commit()
            cls.lab_a = encargado_con_laboratorio(db, 'rep_a', 'QUIMICA', camera_ids=[cam_a.id])
            cls.lab_b = encargado_con_laboratorio(db, 'rep_b', 'FISICA', camera_ids=[cam_b.id])
            db.get(models.Workshop, cls.lab_a).campus_id = campus.id
            db.add(models.WorkshopEquipmentRule(workshop_id=cls.lab_a, equipment_id='guantes', is_required=True))
            cls.campus_id = campus.id

            def alerta(code, cam, lab, status, faults, minutos=0, revisada_en=None, accion=None):
                incident = models.Incident(
                    code=code, workshop_id=lab, camera_id=cam.id, title='Falta EPP', description='x',
                    severity=faults[0][1], status=status, occurred_at=DIA + timedelta(minutes=minutos),
                    reviewed_at=(DIA + timedelta(minutes=minutos, seconds=revisada_en)) if revisada_en else None,
                    action_taken=accion,
                )
                db.add(incident)
                db.flush()
                for equipment_id, severity, detail in faults:
                    db.add(models.IncidentFault(incident_id=incident.id, equipment_id=equipment_id,
                                                severity=severity, detail=detail))
                return incident

            alerta('REP-1', cam_a, cls.lab_a, 'CONFIRMADA', [('guantes', 'ALTO', None), ('bata', 'MEDIO', 'BATA_ABIERTA')],
                   revisada_en=60, accion='CORREGIDO')
            alerta('REP-2', cam_a, cls.lab_a, 'DESCARTADA', [('guantes', 'ALTO', None)], minutos=10, revisada_en=180)
            alerta('REP-3', cam_b, cls.lab_b, 'PENDIENTE', [('lentes', 'CRÍTICO', None)], minutos=20)
            alerta('REP-FUERA', cam_b, cls.lab_b, 'PENDIENTE', [('lentes', 'CRÍTICO', None)], minutos=60 * 24 * 5)
            db.commit()
        cls.admin = headers('admin_reporte', 'ADMINISTRADOR')
        cls.a = headers('rep_a', 'ENCARGADO_QUIMICA')

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _reporte(self, who=None, **params):
        response = self.client.get(URL, params=periodo(**params), headers=who or self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_resumen_con_datos_reales(self):
        data = self._reporte()
        codes = {row['code'] for row in data['incidents']}
        self.assertEqual(codes, {'REP-1', 'REP-2', 'REP-3'})  # REP-FUERA está fuera del periodo
        summary = data['summary']
        self.assertEqual((summary['alerts'], summary['confirmed'], summary['discarded'], summary['pending']), (3, 1, 1, 1))
        self.assertEqual(summary['faults'], 4)
        self.assertEqual(summary['minor_faults'], 1)
        self.assertEqual(summary['faults_by_severity'], {'CRÍTICO': 1, 'ALTO': 2, 'MEDIO': 1})
        self.assertEqual(summary['precision'], 0.5)
        self.assertEqual(summary['avg_response_seconds'], 120)
        self.assertEqual(summary['actions']['CORREGIDO'], 1)
        guantes = next(row for row in data['equipment'] if row['equipment_id'] == 'guantes')
        self.assertEqual((guantes['faults'], guantes['confirmed_faults']), (2, 1))
        lab_a = next(row for row in data['workshops'] if row['workshop_id'] == self.lab_a)
        self.assertEqual((lab_a['alerts'], lab_a['campus_name']), (2, 'Local Reporte'))
        self.assertTrue(lab_a['required_equipment'])
        self.assertEqual(len(data['fingerprint']), 64)

    def test_encargado_solo_su_laboratorio(self):
        data = self._reporte(self.a)
        self.assertEqual({row['code'] for row in data['incidents']}, {'REP-1', 'REP-2'})
        self.assertEqual({row['workshop_id'] for row in data['workshops']}, {self.lab_a})
        otro = self.client.get(URL, params=periodo(workshop_id=self.lab_b), headers=self.a)
        self.assertEqual(otro.status_code, 403)

    def test_filtro_por_local(self):
        data = self._reporte(campus_id=self.campus_id)
        self.assertEqual({row['code'] for row in data['incidents']}, {'REP-1', 'REP-2'})
        self.assertEqual(data['filters']['campus_name'], 'Local Reporte')

    def test_periodo_sin_alertas_sale_en_cero(self):
        data = self._reporte(inicio=DIA - timedelta(days=40), fin=DIA - timedelta(days=30))
        self.assertEqual(data['summary']['alerts'], 0)
        self.assertIsNone(data['summary']['precision'])
        self.assertEqual(data['incidents'], [])

    def test_huella_cambia_si_cambian_los_datos(self):
        antes = self._reporte()['fingerprint']
        self.assertEqual(self._reporte()['fingerprint'], antes)  # misma información, misma huella
        with database.SessionLocal() as db:
            incident = db.query(models.Incident).filter_by(code='REP-3').one()
            incident.status = 'CONFIRMADA'
            db.commit()
        try:
            self.assertNotEqual(self._reporte()['fingerprint'], antes)
        finally:
            with database.SessionLocal() as db:
                db.query(models.Incident).filter_by(code='REP-3').one().status = 'PENDIENTE'
                db.commit()

    def test_fechas_invalidas(self):
        sin_zona = {'start': '2026-03-01T00:00:00', 'end': '2026-03-02T00:00:00'}
        self.assertEqual(self.client.get(URL, params=sin_zona, headers=self.admin).status_code, 422)
        al_reves = periodo(inicio=DIA, fin=DIA - timedelta(days=1))
        self.assertEqual(self.client.get(URL, params=al_reves, headers=self.admin).status_code, 422)


if __name__ == '__main__':
    unittest.main()
