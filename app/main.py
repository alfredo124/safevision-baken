from fastapi import FastAPI, Request
from app.modules.auth_db.router import router as auth_router
from app.core.database import SessionLocal
from app.modules.auth_db.services import log_event

app = FastAPI(title="SafeVision API")

# Middleware para logging
@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)
    
    # Solo logueamos rutas bajo /modules/
    if "/modules/" in request.url.path:
        db = SessionLocal()
        try:
            # Por ahora user_id=1 porque aún no tenemos el contexto del usuario en el middleware
            # En el futuro lo sacaremos del header de autorización
            log_event(db, user_id=1, action=f"{request.method} {request.url.path}", module="API_GATEWAY")
        except Exception as e:
            print(f"Error logueando evento: {e}")
        finally:
            db.close()
            
    return response

# Registrar el router del módulo auth_db
app.include_router(auth_router)

@app.get("/health")
def health_check():
    return {"status": "ok"}
