import sqlite3
import sys
import os
from datetime import datetime

# Añadir el directorio raíz al path para poder importar módulos de la app
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.core.security import get_password_hash

# Configuración de base de datos
DB_PATH = 'safevison_baken/safevision.db'

def run_db_query(query, params=()):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(query, params)
    conn.commit()
    conn.close()

def get_db_content(table):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM {table}")
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    conn.close()
    return columns, rows

# 1. Crear Administrador Inicial
try:
    hashed_admin = get_password_hash('admin123')
    run_db_query(
        "INSERT INTO users (username, hashed_password, full_name, role) VALUES (?, ?, ?, ?)",
        ('admin', hashed_admin, 'Administrador Sistema', 'ADMINISTRADOR')
    )
    admin_id = 1
    print("Administrador creado.")
except sqlite3.IntegrityError:
    # Si ya existe, asumimos que el id es 1
    admin_id = 1
    print("Administrador ya existe.")

# 2. Crear 3 Usuarios de prueba
users_to_create = [
    ('q_enc1', 'pass123', 'Encargado Química 1', 'ENCARGADO_QUIMICA'),
    ('q_enc2', 'pass123', 'Encargado Química 2', 'ENCARGADO_QUIMICA'),
    ('f_enc1', 'pass123', 'Encargado Física 1', 'ENCARGADO_FISICA')
]

for user in users_to_create:
    username, password, full_name, role = user
    hashed_password = get_password_hash(password)
    try:
        run_db_query(
            "INSERT INTO users (username, hashed_password, full_name, role) VALUES (?, ?, ?, ?)",
            (username, hashed_password, full_name, role)
        )
        # Registrar en bitácora
        run_db_query(
            "INSERT INTO activity_logs (user_id, action, module_affected, timestamp) VALUES (?, ?, ?, ?)",
            (admin_id, f"Creación de usuario: {user[0]}", "AUTH", datetime.now())
        )
    except sqlite3.IntegrityError:
        print(f"Usuario {user[0]} ya existe.")

print("Operaciones completadas.\n")

# 3. Mostrar Contenido de Tablas
print("--- Tabla 'users' ---")
cols_u, rows_u = get_db_content('users')
print(cols_u)
for row in rows_u:
    print(row)

print("\n--- Tabla 'activity_logs' ---")
cols_l, rows_l = get_db_content('activity_logs')
print(cols_l)
for row in rows_l:
    print(row)
