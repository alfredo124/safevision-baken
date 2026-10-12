from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.core.database import ensure_camera_schema
from app.modules.auth_db.router import router as auth_router
from app.modules.ai_vision.router import router as ai_vision_router
from app.modules.ai_vision.router_cameras import router as ai_vision_cameras_router

app = FastAPI(title="SafeVision API")

# Configuración de CORS más permisiva
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Endpoint OPTIONS global para pre-flight
@app.options("/{rest_of_path:path}")
async def preflight_handler(rest_of_path: str):
    return {"status": "ok"}

# Registrar rutas
app.include_router(auth_router)
app.include_router(ai_vision_router)
app.include_router(ai_vision_cameras_router)

@app.on_event("startup")
def migrate_camera_schema():
    ensure_camera_schema()

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/favicon.ico")
async def favicon():
    from fastapi import Response
    return Response(status_code=204)
