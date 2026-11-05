"""Las pruebas nunca usan la base real: se fuerza una SQLite temporal antes de importar la app."""
import os
import tempfile

_TEST_DB = os.path.join(tempfile.mkdtemp(prefix='safevision-tests-'), 'test.db')
os.environ['LOCAL_DATABASE_URL'] = f'sqlite:///{_TEST_DB}'
os.environ['SAFEVISION_CAPTURE_DIR'] = os.path.join(os.path.dirname(_TEST_DB), 'capturas')
