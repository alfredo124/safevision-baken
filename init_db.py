from sqlalchemy import create_engine
from app.core.database import engine, Base
from app.modules.auth_db.models import User, ActivityLog
from app.modules.ai_vision.models import Camera

def init_db():
    print("Creando tablas en safevision.db...")
    Base.metadata.create_all(bind=engine)
    print("Tablas creadas correctamente.")

if __name__ == "__main__":
    init_db()
