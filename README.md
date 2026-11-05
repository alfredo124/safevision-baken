# SafeVision: Sistema de Monitoreo de EPP para Laboratorios

## Descripción
SafeVision es un prototipo académico de apoyo al personal de laboratorio. Durante una práctica activa verifica, mediante visión artificial, la **presencia visible** del Equipo de Protección Personal (EPP) exigido en laboratorios de Química y Física y envía alertas al encargado, que confirma o descarta cada una. No graba video y no reemplaza la supervisión humana.

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

Cada regla de un taller puede guardar su código y gravedad según el reglamento (`rules` en `PUT .../configuration`); si faltan, se usan `missing_code` e `impact` del catálogo. Los EPP sin detector (`model_class` vacío), como botas o vestimenta, se marcan como verificación manual y nunca generan alertas.

Para aplicar la regla base de un reglamento revisado (por ahora `quimica-v3.1`) a un taller:

```powershell
python tools/aplicar_reglamento.py                                   # lista talleres
python tools/aplicar_reglamento.py quimica-v3.1 --taller 2            # muestra el cambio
python tools/aplicar_reglamento.py quimica-v3.1 --taller 2 --confirmar
```

### Monitoreo continuo (una práctica activa)
- `POST /modules/reglas_lab/workshops/{id}/monitoring/start`: analiza todas las cámaras del taller **aunque nadie tenga abierta la página de Monitoreo**. Personas y puntos clave con `yolo11n-pose.pt` (se descarga solo la primera vez), EPP con `best.pt`.
- `POST .../monitoring/stop` y `GET .../monitoring`: detener y consultar el estado (análisis hechos, alertas, espectadores).
- Cada falta confirmada por el motor crea una incidencia `PENDIENTE` del sistema, con el código de regla vigente y una captura en `capturas/` (ignorada por Git) con la cabeza de **todas** las personas cubierta. El video no se graba: solo se escribe esa captura.
- `GET /modules/reglas_lab/incidents/{id}/capture`: la captura, solo para quien puede ver ese taller.
- Las reglas se leen al iniciar el monitoreo; si se cambian, detener e iniciar de nuevo.
- Si una alerta sigue pendiente más de `SAFEVISION_ESCALAR_MIN` minutos (por defecto 5), se marca para el administrador; nunca se confirma sola.
- Las cámaras en vivo (webcam, RTSP) se reconectan solas hasta 2 minutos; durante el corte no se analiza ni se generan alertas.
- `SAFEVISION_CAPTURE_DIR` cambia la carpeta de capturas. **Al probar con una copia de la base, usa siempre otra carpeta**: borrar o descartar en la copia borraría las capturas reales.
- **Fase de prueba:** al iniciar, el administrador puede marcar "Borrar incidencias anteriores de este taller" (`clear_previous`), que elimina sus incidencias, faltas y capturas. Para la versión final, desactivarlo con la variable de entorno `SAFEVISION_MODO_PRUEBA=0`.

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
> **Atención:** `app.setup_db` **borra** la base existente.

### Crear el primer administrador
No hay registro público: solo un administrador autenticado puede crear (`POST /modules/auth_db/users`) o modificar (`PATCH /modules/auth_db/users/{username}`) usuarios. Para crear el primero sin tocar los datos existentes:

```powershell
python tools/create_admin.py admin --full-name "Administrador Sistema"
```

La contraseña se pide por teclado (o se lee de `SAFEVISION_ADMIN_PASSWORD`). Si el usuario ya existe, no se modifica.

### Base de datos local
Por defecto se usa `safevison_baken/safevision.db`. La variable `LOCAL_DATABASE_URL` permite usar otra base SQLite, por ejemplo `sqlite:///C:/ruta/otra.db`.

## Pruebas
```powershell
python -m unittest discover -s tests -t .
```
Las pruebas usan una base SQLite temporal y no modifican `safevision.db`. La prueba del umbral con YOLO real se omite si faltan `best.pt` o `videos/videoplayback.mp4`.

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
