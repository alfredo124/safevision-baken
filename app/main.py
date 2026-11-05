from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.core.database import ensure_camera_schema
from app.modules.auth_db.router import router as auth_router
from app.modules.ai_vision.router import router as ai_vision_router
from app.modules.ai_vision.router_cameras import router as ai_vision_cameras_router
from app.modules.reglas_lab.router import router as reglas_lab_router
from app.modules.reglas_lab.services import initialize_reglas_lab

app = FastAPI(title="SafeVision API")

# CORS: la red local (LAN) y los orígenes de SAFEVISION_CORS_ORIGINS. Ya no se acepta "*".
from app.core.config import LAN_ORIGIN_REGEX, allow_lan_origins, cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_origin_regex=LAN_ORIGIN_REGEX if allow_lan_origins() else None,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)

# Registrar rutas
app.include_router(auth_router)
app.include_router(ai_vision_router)
app.include_router(ai_vision_cameras_router)
app.include_router(reglas_lab_router)

@app.on_event("startup")
def migrate_camera_schema():
    ensure_camera_schema()
    initialize_reglas_lab()

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/favicon.ico")
async def favicon():
    from fastapi import Response
    return Response(status_code=204)
