@echo off
rem Backend con HTTPS en el puerto 8000 (necesario para la camara del celular u otro equipo).
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
rem Crea el certificado, o lo renueva si cambio la IP de este PC (otra red, datos del celular).
python tools\crear_certificado.py --si-cambia || (echo [ERROR] No se pudo crear el certificado & pause & exit /b 1)
echo Backend HTTPS: https://localhost:8000   (otros equipos: https://IP-DE-ESTE-PC:8000)
echo Si cambias de red, cierra y vuelve a abrir este archivo y despues el del frontend.
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile certs\safevision.key --ssl-certfile certs\safevision.crt
pause
