from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
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

# Avisos de cambios (cámaras, talleres, práctica, incidencias) para que las pantallas se actualicen solas.
from app.core import events


@app.middleware("http")
async def notify_changes(request: Request, call_next):
    response = await call_next(request)
    topics = events.topics_for_request(request.method, request.url.path, response.status_code)
    if topics:
        events.notify(*topics)
    return response


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """Avisa a cada navegador qué tema cambió ({"type": "changed", "topics": [...]}).
    No envía datos: el navegador los vuelve a pedir con su permiso habitual."""
    import asyncio
    from fastapi import HTTPException
    from app.modules.auth_db.dependencies import get_user_from_token
    try:
        get_user_from_token(websocket.query_params.get('token') or '')
    except HTTPException:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    last = events.versions()
    await websocket.send_json({'type': 'hello', 'versions': last})
    idle = 0.0
    try:
        while True:
            await asyncio.sleep(0.5)
            idle += 0.5
            current = events.versions()
            changed = [topic for topic, version in current.items() if version != last.get(topic)]
            if changed:
                await websocket.send_json({'type': 'changed', 'topics': changed, 'versions': current})
                last, idle = current, 0.0
            elif idle >= 25:  # mantiene viva la conexión a través de proxies y del celular
                await websocket.send_json({'type': 'ping'})
                idle = 0.0
    except (WebSocketDisconnect, RuntimeError):
        pass


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

@app.get("/certificado")
def descargar_certificado():
    """Autoridad local (certs/ca.crt) para instalar en el celular y evitar la advertencia de HTTPS.
    Es un certificado público: no contiene claves."""
    from pathlib import Path
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    path = Path(__file__).resolve().parents[1] / 'certs' / 'ca.crt'
    if not path.is_file():
        raise HTTPException(status_code=404, detail='Ejecuta tools/crear_certificado.py')
    return FileResponse(path, media_type='application/x-x509-ca-cert', filename='SafeVision-LAN.crt')


@app.get("/favicon.ico")
async def favicon():
    from fastapi import Response
    return Response(status_code=204)
