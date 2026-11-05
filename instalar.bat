@echo off
rem Instalación del backend de SafeVision (una sola vez por PC).
rem Crea un entorno virtual .venv, instala las dependencias y el primer administrador.
cd /d "%~dp0"
where python >nul 2>nul || (echo [ERROR] No se encontro Python. Instala Python 3.11 o superior y marca "Add to PATH". & pause & exit /b 1)
if not exist .venv (
  echo Creando entorno virtual .venv ...
  python -m venv .venv || (echo [ERROR] No se pudo crear .venv & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || (echo [ERROR] Fallo la instalacion de dependencias & pause & exit /b 1)
echo.
echo Creando el primer administrador (si ya existe, no se modifica).
python tools\create_admin.py admin --full-name "Administrador Sistema"
echo.
echo Listo. Para arrancar el backend: iniciar.bat
pause
