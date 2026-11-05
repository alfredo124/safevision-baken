import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Obtener la ruta absoluta hacia el directorio 'safevison_baken'
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Aseguramos que la BD esté dentro de 'safevison_baken'
DB_DIR = os.path.join(BASE_DIR, "safevison_baken")
DATABASE_PATH = os.path.join(DB_DIR, "safevision.db")

# LOCAL_DATABASE_URL permite usar otra base SQLite (por ejemplo, en pruebas).
# Sin definirla se usa la ruta histórica safevison_baken/safevision.db.
SQLALCHEMY_DATABASE_URL = os.environ.get("LOCAL_DATABASE_URL") or f"sqlite:///{DATABASE_PATH}"
if SQLALCHEMY_DATABASE_URL == f"sqlite:///{DATABASE_PATH}" and not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def add_missing_columns(table_name: str, columns: dict[str, str]) -> None:
    """Añade columnas nuevas a una tabla existente; create_all no modifica tablas."""
    existing = {column['name'] for column in inspect(engine).get_columns(table_name)}
    with engine.begin() as connection:
        for column_name, column_type in columns.items():
            if column_name not in existing:
                connection.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}'))


def ensure_camera_schema():
    """Add camera configuration columns to databases created by older versions."""
    from app.modules.ai_vision import models  # Register Camera with the shared metadata.

    Base.metadata.create_all(bind=engine)
    existing_columns = {column['name'] for column in inspect(engine).get_columns('cameras')}
    new_columns = {
        'output_stream_url': 'VARCHAR',
        'ip_address': 'VARCHAR',
        'username': 'VARCHAR',
        'password': 'VARCHAR',
        'confidence_threshold': 'FLOAT NOT NULL DEFAULT 0.5',
        'processing_fps': 'INTEGER NOT NULL DEFAULT 10',
        'status': "VARCHAR NOT NULL DEFAULT 'offline'",
        'updated_at': 'DATETIME',
    }
    with engine.begin() as connection:
        connection.execute(text(
            'UPDATE cameras SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL'
        ))
        for column_name, column_type in new_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(
                    f'ALTER TABLE cameras ADD COLUMN "{column_name}" {column_type}'
                ))
        if 'updated_at' not in existing_columns:
            connection.execute(text(
                'UPDATE cameras SET updated_at = COALESCE(created_at, CURRENT_TIMESTAMP) '
                'WHERE updated_at IS NULL'
            ))
