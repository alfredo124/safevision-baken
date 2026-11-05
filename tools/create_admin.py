"""Crea el primer administrador en la base SQLite local.

Uso: python tools/create_admin.py admin --full-name "Administrador Sistema"
La contraseña se pide por teclado (o con la variable SAFEVISION_ADMIN_PASSWORD),
para no dejarla escrita en el código ni en el historial de la terminal.
No modifica usuarios existentes.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import Base, SessionLocal, engine
from app.core.security import get_password_hash
from app.modules.auth_db.models import User, UserRole


def create_admin(username: str, password: str, full_name: str) -> bool:
    """Devuelve True si lo creó y False si el usuario ya existía."""
    Base.metadata.create_all(bind=engine, tables=[User.__table__])
    with SessionLocal() as db:
        if db.query(User).filter(User.username == username).first():
            return False
        db.add(User(
            username=username,
            hashed_password=get_password_hash(password),
            full_name=full_name,
            role=UserRole.ADMINISTRADOR,
            is_active=True,
        ))
        db.commit()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('username')
    parser.add_argument('--full-name', default='Administrador Sistema')
    args = parser.parse_args()

    password = os.environ.get('SAFEVISION_ADMIN_PASSWORD') or getpass.getpass('Contraseña: ')
    if len(password) < 6:
        sys.exit('La contraseña debe tener al menos 6 caracteres.')
    if create_admin(args.username, password, args.full_name):
        print(f'Administrador {args.username} creado.')
    else:
        print(f'El usuario {args.username} ya existe; no se modificó.')


if __name__ == '__main__':
    main()
