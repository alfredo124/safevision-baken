"""Crea certificados HTTPS para la red local (Tarea 0C), sin instalar programas.

Uso: python tools/crear_certificado.py [--si-cambia] [IP extra ...]
  --si-cambia  solo lo renueva si la IP de este PC no está en el certificado o vence pronto
               (lo usa iniciar_https.bat: al cambiar de red, p. ej. a los datos del celular).

Genera en certs/ (fuera de Git):
- ca.crt              autoridad local "SafeVision LAN". Se puede instalar en el celular o en otros PC
                      para que el navegador confíe sin advertencias (opcional).
- safevision.crt/.key certificado del servidor, firmado por esa autoridad, válido para localhost y las IP
                      de este PC. Lo usan el backend (uvicorn) y el frontend (Vite) con HTTPS.
Si la IP del PC cambia (otra red, punto de acceso del celular), se renueva solo el del servidor:
la autoridad se conserva y el celular que ya la instaló no tiene que reinstalarla.
La clave privada de la autoridad (ca.key) no sale de este PC: no la compartas.
"""
import datetime
import ipaddress
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERT_DIR = Path(__file__).resolve().parents[1] / 'certs'
# Los dispositivos Apple rechazan certificados de servidor de más de 398 días.
SERVER_DAYS = 397
CA_DAYS = 3650


def local_ips() -> list[str]:
    ips = {'127.0.0.1'}
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:  # IP de la interfaz con la ruta por defecto (no envía datos)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 80))
            ips.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith('169.254.'))


def _write_key(path: Path, key) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))


def _load_or_create_ca(now):
    ca_key_path, ca_cert_path = CERT_DIR / 'ca.key', CERT_DIR / 'ca.crt'
    if ca_key_path.exists() and ca_cert_path.exists():
        key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        return key, x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'SafeVision LAN (autoridad local)'),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'SafeVision prototipo')])
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                     key_encipherment=False, data_encipherment=False, key_agreement=False,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_key(ca_key_path, key)
    ca_cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert


def previous_ips() -> set[str]:
    """IP del certificado anterior: se conservan para no renovar cada vez que se cambia de red."""
    path = CERT_DIR / 'safevision.crt'
    if not path.exists():
        return set()
    cert = x509.load_pem_x509_certificate(path.read_bytes())
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return {str(ip) for ip in names.get_values_for_type(x509.IPAddress)}


def needs_renewal(ips: list[str], now) -> str | None:
    """Motivo para renovar el certificado del servidor, o None si sigue sirviendo."""
    path = CERT_DIR / 'safevision.crt'
    if not path.exists() or not (CERT_DIR / 'safevision.key').exists():
        return 'no existe'
    cert = x509.load_pem_x509_certificate(path.read_bytes())
    covered = {str(ip) for ip in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
               .value.get_values_for_type(x509.IPAddress)}
    missing = [ip for ip in ips if ip not in covered]
    if missing:
        return 'IP nueva: ' + ', '.join(missing)
    if cert.not_valid_after_utc - now < datetime.timedelta(days=30):
        return 'vence pronto'
    return None


def main() -> None:
    args = sys.argv[1:]
    only_if_changed = '--si-cambia' in args
    extra = [value for value in args if value != '--si-cambia']
    for value in extra:
        ipaddress.ip_address(value)  # valida
    CERT_DIR.mkdir(exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    ips = sorted(set(local_ips()) | set(extra))
    if only_if_changed:
        reason = needs_renewal(ips, now)
        if reason is None:
            print('Certificado vigente para: ' + ', '.join(ips))
            return
        print(f'Renovando el certificado ({reason})...')
        ips = sorted(set(ips) | previous_ips())
    ca_key, ca_cert = _load_or_create_ca(now)
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'SafeVision servidor LAN')]))
        .issuer_name(ca_cert.subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=SERVER_DAYS))
        .add_extension(x509.SubjectAlternativeName(
            [x509.DNSName('localhost'), x509.DNSName(socket.gethostname())]
            + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write_key(CERT_DIR / 'safevision.key', key)
    # Certificado + autoridad (cadena completa) para que los clientes puedan validarla.
    (CERT_DIR / 'safevision.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM)
                                              + ca_cert.public_bytes(serialization.Encoding.PEM))
    print(f'Certificado creado en {CERT_DIR}')
    print('Válido para: localhost, ' + socket.gethostname() + ', ' + ', '.join(ips))
    print(f'Vence: {(now + datetime.timedelta(days=SERVER_DAYS)):%Y-%m-%d}')
    print('Para quitar la advertencia en el celular, instala certs/ca.crt como certificado de autoridad (opcional).')


if __name__ == '__main__':
    main()
