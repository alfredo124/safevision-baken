"""Certificado HTTPS de la red local: se renueva solo si cambia la IP, conservando la autoridad."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import datetime
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location('crear_certificado', Path(__file__).resolve().parents[1] / 'tools' / 'crear_certificado.py')
cert_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cert_tool)


class CertificateRenewalTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='safevision-tests-certs-'))  # nunca la carpeta certs/ real
        self.patch = mock.patch.object(cert_tool, 'CERT_DIR', self.dir)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, ips, *args):
        with mock.patch.object(cert_tool, 'local_ips', return_value=ips), mock.patch('sys.argv', ['x', *args]):
            cert_tool.main()

    def test_renews_only_when_ip_changes_and_keeps_authority(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        self._run(['127.0.0.1', '192.168.1.9'], '--si-cambia')
        ca, server = (self.dir / 'ca.crt').read_bytes(), (self.dir / 'safevision.crt').read_bytes()
        self.assertIsNone(cert_tool.needs_renewal(['127.0.0.1', '192.168.1.9'], now))

        self._run(['127.0.0.1', '192.168.1.9'], '--si-cambia')
        self.assertEqual((self.dir / 'safevision.crt').read_bytes(), server, 'Misma IP: no se toca')

        self.assertIn('192.168.43.20', cert_tool.needs_renewal(['127.0.0.1', '192.168.43.20'], now))
        self._run(['127.0.0.1', '192.168.43.20'], '--si-cambia')  # datos del celular
        self.assertNotEqual((self.dir / 'safevision.crt').read_bytes(), server, 'IP nueva: se renueva')
        self.assertEqual((self.dir / 'ca.crt').read_bytes(), ca, 'La autoridad instalada en el celular sigue sirviendo')
        self.assertTrue({'192.168.1.9', '192.168.43.20'} <= cert_tool.previous_ips(), 'Conserva la red anterior')
        self.assertIsNone(cert_tool.needs_renewal(['127.0.0.1', '192.168.1.9'], now), 'Volver a casa no renueva')


if __name__ == '__main__':
    unittest.main()
