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
Todos los equipos (el PC servidor, otros PC y los celulares) deben estar en **la misma red**. Hay dos casos:

| | Caso A: Wi-Fi de casa o del laboratorio | Caso B: datos compartidos del celular |
|---|---|---|
| Cómo | El PC (por Wi-Fi o cable) y los celulares en el mismo router | Un celular activa *Punto de acceso / Compartir internet*; el PC y los demás se conectan a ese Wi-Fi |
| IP típica del PC | `192.168.1.x`, `192.168.0.x` o `10.x.x.x` | Android: `192.168.43.x` u otra `192.168.x.x` (p. ej. `192.168.158.129`); iPhone: `172.20.10.x` |
| Cuándo usarlo | En casa | Cuando el Wi-Fi no deja que los equipos se vean (frecuente en redes de universidad) o no hay Wi-Fi |

**Qué IP usar (la del PC servidor):**
1. En el PC servidor abre PowerShell y escribe `ipconfig`.
2. Busca el adaptador **conectado a esa red**: *Adaptador de LAN inalámbrica Wi-Fi* (o *Ethernet* si va por cable)
   y copia su **Dirección IPv4**. También la muestra `iniciar.bat` del frontend en la línea *Network*.
3. No uses `127.0.0.1` (solo sirve en el propio PC), ni `169.254.x.x` (sin red), ni las de adaptadores virtuales
   (*vEthernet*, *VirtualBox*, *WSL*).
4. En los demás equipos abre `http://ESA-IP:5173` (o `https://ESA-IP:5173` con HTTPS, sección 4b). La web encuentra
   sola el backend en la misma IP, puerto 8000.

**Una sola vez por PC servidor**, en PowerShell como administrador (abre los puertos también en redes "públicas",
que es como Windows suele clasificar el punto de acceso de un celular):
```powershell
New-NetFirewallRule -DisplayName "SafeVision" -Direction Inbound -Protocol TCP -LocalPort 8000,5173 -Action Allow -Profile Private,Public
```

**Al cambiar de red** (de A a B o al revés) la IP cambia: cierra las ventanas del backend y del frontend y vuelve a
abrir los `.bat` (primero el backend). Con HTTPS el certificado se renueva solo (sección 4b).

Notas del caso B:
- El video entre los equipos va por la red local del celular, no por internet: SafeVision no gasta datos móviles.
  Los equipos conectados sí pueden gastarlos en otras cosas (actualizaciones, otras apps). Algunos celulares
  exigen tener los datos activos para encender el punto de acceso.
- Si los equipos no se ven entre sí, desactiva el *aislamiento de clientes* del punto de acceso (si la opción
  existe) o prueba con otro celular.
- El celular que comparte la red también puede usar SafeVision o ser la cámara; se calienta y gasta batería:
  mejor conectado al cargador.

La **cámara del navegador** de otro equipo o del celular necesita HTTPS: ver la sección siguiente.

### 4b. Usar la cámara del celular u otro equipo (HTTPS)
El navegador solo permite usar la cámara con HTTPS (o en `localhost`). Para la red local:
1. Arranca con HTTPS: `safevison_baken\iniciar_https.bat` y después `safevision-web\iniciar_https.bat`.
   La primera vez se crea el certificado en `safevison_baken\certs\` (fuera de Git; contiene claves privadas).
2. En el celular abre la IP del PC servidor (sección 4) con **https**, por ejemplo `https://192.168.1.9:5173`.
3. El navegador avisará de que la conexión no es privada. Dos opciones:
   - **Rápida:** acepta la advertencia en `https://IP:5173` y también una vez en `https://IP:8000/health`
     (el backend es otra dirección y también hay que aceptarla).
   - **Sin advertencias:** descarga `https://IP:8000/certificado` (o copia `certs\ca.crt`) e instálalo como
     certificado de autoridad. Android: *Ajustes → Seguridad → Cifrado y credenciales → Instalar certificado → CA*.
     iPhone: abrir el archivo, instalar el perfil en *Ajustes* y activarlo en
     *General → Información → Ajustes de confianza de certificados*.
4. Entra en **Estación de cámara**, crea una "cámara de navegador", elige la cámara del dispositivo y pulsa
   *Iniciar transmisión*. La vista local va a la velocidad de la cámara; al servidor solo se envían 2-5 imágenes
   por segundo. Asigna esa cámara a un taller en *Configuración de reglas* para analizarla en la práctica.
5. La transmisión **sigue aunque vayas a Monitoreo u otra página** del mismo navegador: arriba se ve una barra
   verde con el botón *Detener*. Se corta al pulsar *Detener*, cerrar sesión o cerrar/recargar la página.
   Mantén el navegador abierto y la pantalla encendida: en segundo plano puede dejar de enviar. Si la estación se
   corta, la cámara queda "sin señal", no se analiza ni genera alertas, y se reconecta sola.

Si cambias de red (otro Wi-Fi o los datos del celular), cierra y vuelve a abrir `iniciar_https.bat` del backend y
después el del frontend: el backend detecta la IP nueva y renueva el certificado con la autoridad de siempre
(no hay que reinstalarla en el celular). Las IP anteriores se conservan, así que volver a la red de antes no lo renueva.

### 5. Primeros pasos en la aplicación
1. **Monitoreo → Agregar nueva cámara**: carga un video `.mp4` o indica una URL RTSP. Los videos se guardan en
   `videos\cameras\` y no se suben a Git.
2. **Configuración de reglas**: elige el taller, asígnale la cámara y marca los EPP (guantes, lentes, bata).
   Para usar los códigos del reglamento de Química, con el backend detenido y el `.venv` activado:
   `python tools\aplicar_reglamento.py quimica-v3.1 --taller 2 --confirmar`.
3. Pulsa **Iniciar práctica**. Las alertas aparecen en cualquier página: abajo a la derecha en el PC y, en el celular, como una barra plegable bajo la cabecera.

### Configuración opcional
Las variables están documentadas en `.env.example` y se activan quitando `rem` en `iniciar.bat`. Las más útiles:
- `SAFEVISION_MAX_FPS`: análisis por segundo y cámara. **Sin definir, es automático**: al iniciar la primera
  práctica se cronometra el análisis en ese PC (2 en un PC lento, 3-4 con una CPU potente, 5 con GPU). El equipo
  detectado y el límite se ven en el control de práctica. Pon un número solo para fijarlo a mano.
- `SAFEVISION_IMGSZ`: tamaño de imagen para YOLO (480 por defecto desde el 2026-11-12; antes 640). Con los
  videos etiquetados del equipo, 480 y 640 dan exactamente las mismas faltas y falsas alarmas, y 480 es más rápido.
- `SAFEVISION_ANALISIS_SIMULTANEOS`: cámaras que analizan a la vez. **Sin definir, es automático** (ver
  abajo). Pon un número solo para fijarlo a mano.
- `SAFEVISION_ESCALAR_MIN`: minutos sin respuesta antes de avisar al administrador (5 por defecto).

### Rendimiento: por qué una cámara va lenta
El video de todas las cámaras va siempre fluido; lo que se reparte es el **análisis** (recuadros y alertas).
Las cámaras de la práctica se **turnan** (`app/modules/ai_vision/turnos.py`): solo unas pocas analizan a la
vez. Primero va cualquier cámara que lleve 4 s sin analizarse; después, la cámara ampliada en Monitoreo
(como máximo 1 de cada 3 turnos); si no, la que más espera.

**Cuántas analizan a la vez se decide solo en cada PC** (`performance.plan_analysis`), al iniciar la primera
práctica tras arrancar el backend (unos segundos más esa vez): se cuentan los núcleos físicos, se miden 1, 2
y hasta núcleos ÷ 4 análisis a la vez (con GPU, hasta 2), y se usa lo que más rinda. Hay una copia del
modelo de EPP por análisis simultáneo. La decisión se ve en el control de práctica y en
`GET /modules/ai_vision/rendimiento`. Medido el 2026-11-12 con 5 videos en un PC de 2 núcleos sin GPU:
todas a la vez, 0.20 análisis/s por cámara (y 0.04-0.08 al ampliar una); de 1 en 1, 0.36/s con pausas de
hasta 11 s; con 2 a la vez (lo que eligió solo), ~0.55/s por cámara, ninguna espera más de ~2.5 s y la
ampliada se analiza cada ~1.3 s.
El motor confirma una falta con 8 s de muestras dentro de una ventana de 12 s, así que funciona igual con
una muestra cada 0.5 s o cada 4.5 s. Para más cámaras: asigna al taller solo las que se usan o usa una GPU.

### PC con GPU NVIDIA (opcional, mucho más rápido)
`requirements.txt` instala PyTorch para CPU. Con una GPU NVIDIA y el `.venv` activado:
```powershell
python -m pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu128
```
Elige la versión de CUDA en https://pytorch.org. Ultralytics usa la GPU automáticamente y SafeVision sube solo el
límite de análisis. Si el PC tiene GPU NVIDIA pero PyTorch es el de CPU, el control de práctica lo avisa.

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
- `app/core/`: configuración por variables de entorno, seguridad (JWT), conexión a la base SQLite, permisos y bitácora (`bitacora.py`).
- `app/modules/auth_db/`: usuarios, roles, inicio de sesión y consulta de la bitácora (`GET /modules/auth_db/activity`, solo administrador).
- `app/modules/ai_vision/`: cámaras, captura de video, detectores (pose + `best.pt`), capturas enmascaradas.
- `app/modules/reglas_lab/`: cumplimiento de EPP en los laboratorios, dividido en submódulos con un solo `models.py`:

  ```
  reglas_lab/
  ├── models.py          tablas de todo el módulo (compartidas)
  ├── comun.py           sesión de BD, permisos sobre un laboratorio, fechas UTC, tipos comunes
  ├── inicializacion.py  crea tablas, añade columnas nuevas y carga el catálogo inicial al arrancar
  ├── router.py          une los routers de los submódulos (/modules/reglas_lab)
  ├── cumplimiento/      motor.py (¿lleva el EPP cada persona?) y overlay.py (datos para dibujar en el video)
  ├── laboratorios/      laboratorios, locales, catálogo de EPP, reglas exigidas y reglamentos
  ├── practicas/         iniciar/finalizar práctica: monitoreo.py (análisis) y sesiones.py (lo guardado)
  ├── incidencias/       alertas: servicio.py (registro automático y agrupación) y validación
  └── reportes/          reporte SST (reporte.py), emisión y verificación
  ```
- `tools/`: utilidades (crear administrador, aplicar reglamento, grabar y evaluar detecciones de videos).
- `tests/`: pruebas automáticas.

## Módulo de reglas de laboratorio
El módulo `app/modules/reglas_lab/` persiste talleres, equipos EPP requeridos, asignaciones a cámaras reales e incidencias. Al iniciar el backend crea las tablas y, solo si aún no existen talleres, carga los talleres iniciales y vincula las cámaras activas según su área (`QUIMICA` o `FISICA`).

Rutas principales, protegidas con JWT:
- `GET /modules/reglas_lab/configuration`: talleres, catálogo EPP y cámaras disponibles.
- `POST /modules/reglas_lab/workshops` y `PUT /modules/reglas_lab/workshops/{id}/configuration`: crear talleres y guardar cámaras/EPP requeridos.
- `POST /modules/reglas_lab/equipment`: ampliar el catálogo EPP (solo administrador).
- `GET/POST /modules/reglas_lab/incidents`: consultar (filtros `start`/`end` con zona horaria, `session_id`, `limit`) y registrar incidencias.
- `POST /modules/reglas_lab/incidents/{id}/review`: confirmar (con la acción tomada) o descartar una alerta. Es la única forma de cambiar su estado.

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
- Las incidencias, faltas y capturas se conservan siempre: iniciar o detener una práctica no borra nada (la antigua fase de prueba con `clear_previous` se eliminó el 2026-11-12). Solo al **descartar** una alerta se borran sus imágenes.

### Cómo decide el motor (revisión del 2026-11-12)
- **Umbral del detector:** durante la práctica el EPP se detecta con 0.25, el umbral con que se calibró el
  motor. El umbral de cada cámara (0.5 por defecto) solo afecta a los recuadros de la vista. Antes se usaba
  el de la cámara y se perdían el 20-35 % de las detecciones de EPP, que el motor contaba como "no lo lleva".
- **Bata por color:** el detector mide qué parte del torso es blanca o gris clara (`torso_light`). Torso
  oscuro (< 0.3): la "bata" detectada es otra prenda y no cuenta. Torso cubierto y claro (≥ 0.8): bata puesta,
  sin mirar la confianza del modelo. Entre ambos: bata abierta (falta leve). Si el modelo no ve la bata pero
  el torso es claro, esa imagen no cuenta como prueba de que falte. Medido: sin bata 0.00-0.04, bata abierta
  0.48-0.85, cerrada 0.86-1.00. Una camisa blanca sin bata puede pasar por bata.
- **Personas parciales:** con pose, una persona solo se evalúa si se ve al menos un hombro (los primeros
  planos de manos o de cara se ignoran).
- **Lentes:** `best.pt` casi no detecta lentes puestos (4 de 78 imágenes en los videos del equipo); sus
  alertas tienen muchas falsas alarmas hasta reentrenar el modelo.
- Para calibrar: `tools/record_detections.py` (graba detecciones, ya con el color del torso),
  `tools/agregar_color_torso.py` (añade el color a grabaciones anteriores) y `tools/evaluate_labels.py`
  (compara con tramos etiquetados en `grabaciones/*.labels.json`).

### Reporte SST
- `GET /modules/reglas_lab/report?start=...&end=...` (opcional `campus_id`, `workshop_id`): resumen del periodo `[start, end)`
  calculado con las incidencias guardadas (alertas, confirmadas, falsas alarmas, pendientes, faltas por gravedad y por EPP,
  tiempo medio de respuesta), resultados por laboratorio y bitácora (hasta 300 filas). Las fechas llevan zona horaria: la
  web envía el inicio y el fin del día en su hora local. El encargado solo obtiene su laboratorio.
- Incluye una huella SHA-256 del contenido (sin la hora de emisión): si una alerta del periodo cambia, la huella cambia.
  La lógica está en `app/modules/reglas_lab/reportes/reporte.py`.
- El EPP exigido de cada laboratorio sale de la copia de reglas que guarda cada práctica (lo que se exigía entonces); si en
  el periodo no hubo prácticas con copia, se usa la configuración actual (`required_equipment_source = ACTUAL`).
- `POST /modules/reglas_lab/reports`: emite el reporte (la web lo hace al abrir el documento). Queda guardado en `lab_reports`
  con su código, periodo, laboratorios, responsable SST y huella. `GET /reports` los lista y `GET /reports/{code}/verify`
  lo recalcula y dice si la huella sigue coincidiendo.

Las incidencias admiten clase/confianza YOLO y `bounding_box` (`[x1, y1, x2, y2]`) con dimensiones del frame. Estos campos quedan persistidos para que la interfaz pueda recuperar los datos al dibujar cajas propias. Las cámaras con incidencias históricas no se pueden eliminar.
