import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy import create_engine
from app.core.database import Base, SQLALCHEMY_DATABASE_URL, DATABASE_PATH
from app.modules.auth_db.models import User, ActivityLog
from app.modules.ai_vision.models import Camera
from app.core.security import get_password_hash

def reset_and_seed():
    # 1. Eliminar base de datos existente
    if os.path.exists(DATABASE_PATH):
        os.remove(DATABASE_PATH)
        print(f"Base de datos eliminada en: {DATABASE_PATH}")

    # 2. Crear tablas
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    print("Esquema recreado correctamente.")

    # 3. Insertar datos (Seed)
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Admin
    hashed_admin = get_password_hash('admin123')
    cursor.execute('INSERT INTO users (username, hashed_password, full_name, role) VALUES (?, ?, ?, ?)', 
                   ('admin', hashed_admin, 'Administrador Sistema', 'ADMINISTRADOR'))

    # Cámaras
    video_path = os.path.join(PROJECT_ROOT, 'videos', 'videoplayback.mp4')
    # Normalizar ruta para SQLite
    video_path = video_path.replace('\\', '/')

    cursor.execute('INSERT INTO cameras (name, laboratory_type, stream_url, is_active, overlay_enabled) VALUES (?, ?, ?, ?, ?)', 
                   ('Camara 01 (Con Cajas)', 'QUIMICA', video_path, True, True))
    cursor.execute('INSERT INTO cameras (name, laboratory_type, stream_url, is_active, overlay_enabled) VALUES (?, ?, ?, ?, ?)', 
                   ('Camara 02 (Limpia)', 'QUIMICA', video_path, True, False))

    conn.commit()
    conn.close()
    print("Datos semilla insertados correctamente.")

if __name__ == "__main__":
    reset_and_seed()
