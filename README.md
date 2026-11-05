# SafeVision: Sistema de Monitoreo de EPP para Laboratorios

## Descripción
SafeVision es un prototipo académico de apoyo al personal de laboratorio. Durante una práctica activa verifica, mediante visión artificial, la **presencia visible** del Equipo de Protección Personal (EPP) exigido en laboratorios de Química y Física y envía alertas al encargado, que confirma o descarta cada una. No graba video y no reemplaza la supervisión humana.

## Guía rápida de instalación (para el equipo)

Probado en Windows 10 con Python 3.14 y Node.js 24, sin GPU. Los dos repositorios van en la misma carpeta:

```text
C:\git\universidad\
├── safevison_baken\   (este repositorio: backend)
└── safevision-web\    (frontend)
```

### 1. Requisitos (una vez por PC)
- **Python 3.11 o superior** (https://www.python.org). Al instalar, marca *Add python.exe to PATH*.
- **Node.js 20 o superior, versión LTS** (https://nodejs.org).
- **Git**, para clonar los dos repositorios:
  ```powershell
  git clone https://github.com/alfredo124/safevision-baken.git safevison_baken
  git clone https://github.com/alfredo124/safevision-web.git safevision-web
  ```
- Internet la primera vez: se descargan las librerías (PyTorch pesa ~1 GB) y el modelo `yolo11n-pose.pt`.

### 2. Instalar (una vez)
1. **Backend:** doble clic en `safevison_baken\instalar.bat`. Crea `.venv`, instala `requirements.txt` y pide la
   contraseña del usuario `admin`; no se ve al escribirla.
2. **Frontend:** doble clic en `safevision-web\instalar.bat`, que ejecuta `npm install`.

### 3. Arrancar (cada vez)
1. Doble clic en `safevison_baken\iniciar.bat`: backend en el puerto 8000. Deja la ventana abierta.
2. Doble clic en `safevision-web\iniciar.bat`: frontend en el puerto 5173. Deja la ventana abierta.
3. Abre http://localhost:5173 y entra con `admin`.

La primera vez que se inicia una práctica, Ultralytics descarga `yolo11n-pose.pt` (6 MB).

### 4. Usarlo desde otros equipos o el celular (misma red)
- En el PC servidor, **PowerShell como administrador**, una sola vez:
  ```powershell
  New-NetFirewallRule -DisplayName "SafeVision" -Direction Inbound -Protocol TCP -LocalPort 8000,5173 -Action Allow -Profile Private,Public
  ```
- Los demás equipos abren la dirección *Network* que muestra `iniciar.bat` del frontend, por ejemplo
  `http://192.168.1.9:5173`. La web encuentra sola el backend en la misma IP, puerto 8000.
- Funciona igual con el punto de acceso de un celular, incluso sin internet. Si los equipos no se ven entre sí,
  desactiva el *aislamiento de clientes* del punto de acceso.
- La **cámara del navegador** de otro equipo o del celular necesita HTTPS (Tarea 0C, en preparación).

### 5. Primeros pasos en la aplicación
1. **Monitoreo → Agregar nueva cámara**: carga un video `.mp4` o indica una URL RTSP. Los videos se guardan en
   `videos\cameras\` y no se suben a Git.
2. **Configuración de reglas**: elige el taller, asígnale la cámara y marca los EPP (guantes, lentes, bata).
   Para usar los códigos del reglamento de Química, con el backend detenido y el `.venv` activado:
   `python tools\aplicar_reglamento.py quimica-v3.1 --taller 2 --confirmar`.
3. Pulsa **Iniciar práctica**. Las alertas aparecen abajo a la derecha, en cualquier página.

### Configuración opcional
Las variables están documentadas en `.env.example` y se activan quitando `rem` en `iniciar.bat`. Las más útiles:
- `SAFEVISION_MAX_FPS`: análisis por segundo y cámara (2 por defecto; con GPU, 4 o 5).
- `SAFEVISION_ESCALAR_MIN`: minutos sin respuesta antes de avisar al administrador (5 por defecto).
- `SAFEVISION_MODO_PRUEBA=0`: versión final, no permite borrar incidencias.

### PC con GPU NVIDIA (opcional, mucho más rápido)
`requirements.txt` instala PyTorch para CPU. Con una GPU NVIDIA y el `.venv` activado:
```powershell
python -m pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu128
```
Elige la versión de CUDA en https://pytorch.org. Ultralytics usa la GPU automáticamente; después sube
`SAFEVISION_MAX_FPS`.

### Lo que no está en Git, y por qué
- `safevision.db` (base local), `capturas\`, `videos\`, `grabaciones\` y `yolo11n-pose.pt`: son datos locales,
  material de prueba o archivos que se descargan solos. Cada PC tiene su propia base, y `instalar.bat` crea el
  administrador.
- Para llevar los datos a otro PC, copia `safevison_baken\safevision.db` con el backend detenido. Las cámaras de video
  se encuentran aunque el proyecto esté en otra carpeta, si copias también `videos\cameras\`.

## Pruebas automáticas
Con el `.venv` activado (`.venv\Scripts\activate`):
```powershell
python -m unittest discover -s tests -t .
```
Usan una base SQLite temporal y no modifican `safevision.db`. La prueba del umbral con YOLO real se omite si faltan
`best.pt` o `videos\videoplayback.mp4`.

## Comandos manuales (equivalentes a los .bat)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # si PowerShell lo bloquea, usa .venv\Scripts\activate.bat desde cmd
python -m pip install -r requirements.txt
python tools\create_admin.py admin --full-name "Administrador Sistema"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Para comprobar que responde: http://localhost:8000/health debe devolver `{"status":"ok"}`. La documentación de la
API está en http://localhost:8000/docs.

### Usuarios
No hay registro público: solo un administrador autenticado crea (`POST /modules/auth_db/users`) o modifica
(`PATCH /modules/auth_db/users/{username}`) usuarios; por ahora, desde http://localhost:8000/docs.
`tools\create_admin.py` crea un administrador sin tocar los existentes. La contraseña se pide por teclado o se lee de
`SAFEVISION_ADMIN_PASSWORD`.

### Base de datos
Por defecto es `safevison_baken\safevision.db` y se crea sola al arrancar. `LOCAL_DATABASE_URL` permite usar otra,
por ejemplo `sqlite:///C:/ruta/otra.db`. `python -m app.setup_db` **borra** la base y la recrea con datos semilla:
no lo uses sobre datos que quieras conservar.

## Detalles de red (LAN, CORS, Vercel)
- CORS acepta el propio equipo y las IP privadas (192.168.x.x, 10.x.x.x y 172.16-31.x.x). Para otros orígenes, como
  un dominio de Vercel o de un túnel: `SAFEVISION_CORS_ORIGINS=https://a.example,https://b.example`.
  `SAFEVISION_CORS_LAN=0` desactiva los orígenes de la LAN.
- La cuadrícula de Monitoreo pide una imagen por segundo por cámara (`GET /modules/ai_vision/snapshot/{id}`) y solo
  la cámara ampliada usa video continuo, para no agotar las ~6 conexiones por servidor del navegador.
- Una web en Vercel (HTTPS) **no** puede usar este backend por `http://`, porque el navegador bloquea el contenido
  mixto. Haría falta un túnel HTTPS; se deja para cuando la seguridad esté cerrada.

## Solución de problemas
- *No se encontró Python* o `python` no reconocido: reinstala Python marcando *Add to PATH*.
- `ModuleNotFoundError: No module named 'app'`: ejecuta desde la carpeta `safevison_baken`.
- La web dice *Error al conectar con el servidor*: revisa que la ventana de `iniciar.bat` del backend siga abierta y,
  si es otro equipo, la regla del firewall.
- El celular no abre la página: misma red Wi-Fi, regla del firewall y aislamiento de clientes del punto de acceso.
- Una cámara de video no carga en otro PC: copia el archivo a `videos\cameras\` o vuelve a subirlo.
- Puerto ocupado: cierra la otra ventana de `iniciar.bat` o el proceso que use el puerto 8000 o 5173.

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
- **Frontend:** React + Vite (`safevision-web`), servido en la red local; dibuja los recuadros del motor sobre el video.

## Capacidades de Video
- **Procesamiento de Archivos:** Soporte para carga y análisis de archivos `.mp4`.
- **Transmisión en Vivo:** Capacidad para procesar flujos de video desde cámaras conectadas al equipo servidor.
- **Registro desde el frontend:** En Monitoreo se pueden registrar cámaras RTSP/HTTP o cargar videos para reproducirlos como streams procesados. Los videos se guardan en `videos/cameras/` y el límite por archivo es 500 MB.

## Estructura del Backend (Resumen)
- `app/main.py`: aplicación FastAPI (rutas y CORS).
- `app/core/`: configuración por variables de entorno, seguridad (JWT) y conexión a la base SQLite.
- `app/modules/auth_db/`: usuarios, roles e inicio de sesión.
- `app/modules/ai_vision/`: cámaras, captura de video, detectores (pose + `best.pt`), capturas enmascaradas.
- `app/modules/reglas_lab/`: talleres, reglas, motor de cumplimiento (`compliance.py`), monitoreo e incidencias.
- `tools/`: utilidades (crear administrador, aplicar reglamento, grabar y evaluar detecciones de videos).
- `tests/`: pruebas automáticas.

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
