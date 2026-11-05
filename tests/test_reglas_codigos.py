"""Códigos de regla del reglamento, vestimenta manual y propuesta confirmada. SQLite temporal."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app.core import database
from app.core.security import create_access_token
from app.main import app
from app.modules.reglas_lab.compliance import ComplianceEngine, EquipmentRule
from app.modules.reglas_lab.models import WorkshopEquipmentRule
from tests.test_compliance_pose import posed
from tests.test_compliance import run
from tools.aplicar_reglamento import apply

BASE = '/modules/reglas_lab'


def _headers(role='ADMINISTRADOR', username='admin_reglas'):
    return {'Authorization': f"Bearer {create_access_token({'sub': username, 'role': role})}"}


class RuleCodesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        cls.client = TestClient(app)
        cls.client.__enter__()
        from tools.create_admin import create_admin
        create_admin('admin_reglas', 'admin-pass', 'Admin Reglas')
        response = cls.client.post(f'{BASE}/workshops', headers=_headers(),
                                   json={'name': 'Química pruebas códigos', 'laboratory_type': 'QUIMICA'})
        assert response.status_code == 201, response.text
        cls.workshop_id = response.json()['id']

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _put(self, payload):
        response = self.client.put(f'{BASE}/workshops/{self.workshop_id}/configuration', headers=_headers(), json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return {rule['equipment_id']: rule for rule in response.json()['rules']}

    def test_catalog_has_manual_clothing(self):
        catalog = self.client.get(f'{BASE}/configuration', headers=_headers()).json()['catalog']
        clothing = next(item for item in catalog if item['id'] == 'vestimenta')
        self.assertIsNone(clothing['model_class'])

    def test_rule_codes_are_saved_and_fall_back_to_catalog(self):
        rules = self._put({'rules': [
            {'equipment_id': 'guantes', 'rule_code': 'RLQ-2.3', 'severity': 'ALTO'},
            {'equipment_id': 'chaleco'},
            {'equipment_id': 'vestimenta', 'rule_code': 'RLQ-2.4'},
        ]})
        self.assertEqual(rules['guantes']['rule_code'], 'RLQ-2.3')
        self.assertEqual(rules['chaleco']['rule_code'], 'ERR-EPP-03')  # del catálogo
        self.assertEqual(rules['chaleco']['severity'], 'MEDIO')
        self.assertTrue(rules['vestimenta']['manual_verification'])
        self.assertFalse(rules['guantes']['manual_verification'])

    def test_ids_only_update_keeps_existing_codes(self):
        self._put({'rules': [{'equipment_id': 'guantes', 'rule_code': 'RLQ-2.3'}, {'equipment_id': 'lentes'}]})
        rules = self._put({'required_equipment_ids': ['guantes', 'chaleco']})
        self.assertEqual(set(rules), {'guantes', 'chaleco'})
        self.assertEqual(rules['guantes']['rule_code'], 'RLQ-2.3')

    def test_reglamento_requires_confirmation(self):
        self._put({'required_equipment_ids': ['botas']})
        lines = apply('quimica-v3.1', self.workshop_id, confirm=False)
        self.assertIn('se quita botas', '\n'.join(lines))
        with database.SessionLocal() as db:
            self.assertEqual([r.equipment_id for r in db.query(WorkshopEquipmentRule).filter_by(workshop_id=self.workshop_id)], ['botas'])

        apply('quimica-v3.1', self.workshop_id, confirm=True)
        workshops = self.client.get(f'{BASE}/configuration', headers=_headers()).json()['workshops']
        workshop = next(w for w in workshops if w['id'] == self.workshop_id)
        workshop_rules = {rule['equipment_id']: rule for rule in workshop['rules']}
        self.assertEqual({k: v['rule_code'] for k, v in workshop_rules.items()},
                         {'chaleco': 'RLQ-2.1', 'lentes': 'RLQ-2.2', 'guantes': 'RLQ-2.3', 'vestimenta': 'RLQ-2.4'})


class SchemaMigrationTest(unittest.TestCase):
    def test_old_rules_table_gets_new_columns(self):
        path = Path(tempfile.mkdtemp(prefix='safevision-tests-')) / 'old.db'
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE lab_workshop_equipment_rules (id INTEGER PRIMARY KEY, workshop_id INTEGER, '
                               'equipment_id VARCHAR, is_required BOOLEAN)')
            connection.execute("INSERT INTO lab_workshop_equipment_rules VALUES (1, 1, 'guantes', 1)")
        old_engine = create_engine(f'sqlite:///{path}')
        original = database.engine
        database.engine = old_engine
        try:
            database.add_missing_columns('lab_workshop_equipment_rules', {'rule_code': 'VARCHAR(40)', 'severity': 'VARCHAR(20)'})
            database.add_missing_columns('lab_workshop_equipment_rules', {'rule_code': 'VARCHAR(40)'})  # idempotente
        finally:
            database.engine = original
        columns = {column['name'] for column in inspect(old_engine).get_columns('lab_workshop_equipment_rules')}
        self.assertTrue({'rule_code', 'severity'} <= columns)
        with old_engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql('SELECT count(*) FROM lab_workshop_equipment_rules').scalar(), 1)
        old_engine.dispose()


class EngineCarriesRuleCode(unittest.TestCase):
    def test_violation_event_has_rule_code(self):
        engine = ComplianceEngine([EquipmentRule('guantes', 'manos', 'gloves', 'ALTO', rule_code='RLQ-2.3')])
        events, _ = run(engine, 20, posed())
        self.assertEqual(events[0].rule_code, 'RLQ-2.3')


if __name__ == '__main__':
    unittest.main()
