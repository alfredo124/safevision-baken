@echo off
rem Arranca el backend de SafeVision en el puerto 8000, accesible desde la red local.
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat

rem --- Configuración opcional (quita "rem" para usarla) ---
rem Análisis por segundo y cámara (2 por defecto; con GPU, 4 o 5):
rem set SAFEVISION_MAX_FPS=4
rem Minutos sin respuesta antes de avisar al administrador:
rem set SAFEVISION_ESCALAR_MIN=5
rem Versión final: desactiva el borrado de incidencias de prueba:
rem set SAFEVISION_MODO_PRUEBA=0
rem Orígenes extra permitidos (por ejemplo Vercel o un túnel):
rem set SAFEVISION_CORS_ORIGINS=https://mi-app.vercel.app

echo Backend: http://localhost:8000   (otros equipos: http://IP-DE-ESTE-PC:8000)
echo Documentacion de la API: http://localhost:8000/docs
echo Para detenerlo: Ctrl+C
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
