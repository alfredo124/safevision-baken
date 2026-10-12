# SafeVision: Sistema de Monitoreo de EPP para Laboratorios

## Descripción
SafeVision es un sistema automatizado diseñado para la supervisión y monitoreo en tiempo real del uso correcto de Equipos de Protección Personal (EPP) en laboratorios de Química y Física. El sistema garantiza el cumplimiento de normas de seguridad mediante visión artificial.

## Objetivos
- Automatizar la detección de EPP mediante el procesamiento de video.
- Mantener una bitácora de auditoría detallada de las actividades en los laboratorios.
- Proporcionar una plataforma centralizada para la gestión de usuarios (Administradores y Encargados).
- Integrar visión artificial de alto rendimiento en el backend para un análisis eficiente.

## Arquitectura Técnica
- **Backend:** Python + FastAPI (Modular y desacoplado).
- **Motor de IA:** YOLO con el modelo entrenado `best.pt` para detectar guantes, gafas, bata de laboratorio y personas.
- **Base de Datos:** SQLAlchemy (SQLite para desarrollo inicial) con persistencia de logs de auditoría.
- **Seguridad:** Autenticación mediante JWT (JSON Web Tokens).
- **Frontend:** React + Vite (desplegado en Vercel) para la visualización de resultados e inferencias.

## Capacidades de Video
- **Procesamiento de Archivos:** Soporte para carga y análisis de archivos `.mp4`.
- **Transmisión en Vivo:** Capacidad para procesar flujos de video desde cámaras conectadas al equipo servidor.
- **Registro desde el frontend:** En Monitoreo se pueden registrar cámaras RTSP/HTTP o cargar videos para reproducirlos como streams procesados. Los videos se guardan en `videos/cameras/` y el límite por archivo es 500 MB.

## Estructura del Backend (Resumen)
- `app/main.py`: API Gateway central (gestiona rutas y middleware de logs).
- `app/core/`: Configuración global, seguridad (JWT) y conexión a base de datos.
- `app/modules/`: Lógica de negocio modularizada (auth_db, vision_engine, quimica, fisica).

## Módulo de reglas de laboratorio
El módulo `app/modules/reglas_lab/` persiste talleres, equipos EPP requeridos, asignaciones a cámaras reales e incidencias. Al iniciar el backend crea las tablas y, solo si aún no existen talleres, carga los talleres iniciales y vincula las cámaras activas según su área (`QUIMICA` o `FISICA`).

Rutas principales, protegidas con JWT:
- `GET /modules/reglas_lab/configuration`: talleres, catálogo EPP y cámaras disponibles.
- `POST /modules/reglas_lab/workshops` y `PUT /modules/reglas_lab/workshops/{id}/configuration`: crear talleres y guardar cámaras/EPP requeridos.
- `POST /modules/reglas_lab/equipment`: ampliar el catálogo EPP (solo administrador).
- `GET/POST /modules/reglas_lab/incidents`: consultar y registrar incidencias.
- `PATCH /modules/reglas_lab/incidents/{id}/status`: actualizar su estado.

Las incidencias admiten clase/confianza YOLO y `bounding_box` (`[x1, y1, x2, y2]`) con dimensiones del frame. Estos campos quedan persistidos para que la interfaz pueda recuperar los datos al dibujar cajas propias. Las cámaras con incidencias históricas no se pueden eliminar.

## Requisitos previos
Antes de ejecutar el proyecto, asegúrate de tener instalado:
- Python 3.10 o superior
- pip actualizado
- Git
- Opcional: entorno virtual (`venv`)

## Instalación de dependencias
Desde la raíz del proyecto `safevison_baken` ejecuta:

### Windows (PowerShell)
```powershell
cd c:\git\universidad\safevison_baken
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Linux / macOS
```bash
cd /ruta/al/proyecto/safevison_baken
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> El archivo `requirements.txt` contiene todas las librerías necesarias para ejecutar el backend.
> El checkpoint entrenado debe estar en la raíz de `safevison_baken` con el nombre `best.pt`.

## Inicialización de la base de datos
Si quieres recrear la base de datos con datos semilla:

```bash
python -m app.setup_db
```

Esto crea o reinicia la base de datos y genera los usuarios y cámaras iniciales del sistema.

## Ejecución del backend
Desde la carpeta principal del proyecto:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Una vez levantado, la API quedará disponible en:
- http://localhost:8000
- http://127.0.0.1:8000

También puedes verificar que el backend responde con:

```bash
curl http://127.0.0.1:8000/health
```

La respuesta esperada es:

```json
{"status":"ok"}
```

## Variables de entorno y entorno local
Para desarrollo local, normalmente no hace falta configurar variables complejas, pero si el proyecto se ejecuta en otros equipos, conviene mantener las carpetas del proyecto intactas y usar siempre la carpeta `videos/` ubicada dentro de `safevison_baken`.

## Solución rápida de problemas comunes
- Si aparece `ModuleNotFoundError: No module named 'app'`, asegúrate de ejecutar desde la carpeta `safevison_baken`.
- Si aparece error al borrar la base de datos, cierra cualquier proceso que tenga abierto `safevision.db` (por ejemplo, un servidor Uvicorn activo).
- Si la ruta del video no carga, revisa que exista el archivo `safevison_baken/videos/videoplayback.mp4`.

## Estructura recomendada para colaboradores
```text
safevison_baken/
├── app/
├── videos/
├── requirements.txt
├── README.md
├── .venv/
└── safevison_baken/
```

Con estas instrucciones, cualquier colaborador puede clonar el proyecto, instalar dependencias y ejecutar el backend de forma consistente en su entorno local.
