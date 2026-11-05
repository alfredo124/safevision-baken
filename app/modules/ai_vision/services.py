"""Captura de video y análisis por cámara.

Una sola captura y un solo análisis por cámara, sin importar cuántos clientes miren.
El monitoreo (start_monitoring) es una suscripción del sistema: mantiene la cámara
abierta y analizando aunque nadie tenga abierta la página de Monitoreo.
Los frames solo viven en memoria; nunca se escriben en disco desde aquí.
"""
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from uuid import uuid4

import cv2

from app.modules.reglas_lab.compliance import ComplianceConfig, ComplianceEngine, EquipmentRule

logger = logging.getLogger(__name__)

def _max_fps_from_env() -> float:
    import os
    try:
        return max(0.5, float(os.environ.get('SAFEVISION_MAX_FPS', '2')))
    except ValueError:
        return 2.0


def _image_size_from_env() -> int:
    """Tamaño de imagen para YOLO (SAFEVISION_IMGSZ, 640 por defecto). Múltiplo de 32.
    480 es ~1.8 veces más rápido en CPU; se decide con el video de calibración (Tarea 1)."""
    import os
    try:
        value = int(os.environ.get('SAFEVISION_IMGSZ', '640'))
    except ValueError:
        return 640
    return min(1280, max(320, round(value / 32) * 32))


# Límite de análisis por cámara. Automático según la velocidad medida del equipo (performance.py);
# SAFEVISION_MAX_FPS, si está definida, manda. MAX_PROCESSING_FPS queda como valor inicial.
MAX_PROCESSING_FPS = _max_fps_from_env()


def max_processing_fps() -> float:
    import os
    from app.modules.ai_vision import performance
    if os.environ.get('SAFEVISION_MAX_FPS'):
        return _max_fps_from_env()
    return performance.profile()['auto_max_fps']


def performance_summary() -> dict:
    import os
    from app.modules.ai_vision import performance
    return {**performance.profile(), 'max_fps': max_processing_fps(),
            'manual': bool(os.environ.get('SAFEVISION_MAX_FPS')), 'imgsz': _image_size_from_env()}

# Sincronización video/recuadros durante la práctica: el video se retrasa lo mismo
# que tarda el análisis, para que cada recuadro coincida con el frame analizado.
MAX_SYNC_DELAY = 4.0

# Reconexión de cámaras en vivo (webcam, RTSP): esperas crecientes hasta este total.
RECONNECT_MAX_SECONDS = 120.0
RECONNECT_MAX_WAIT = 10.0
RECONNECT_FIRST_WAIT = 1.0

# Miniaturas: la cámara se mantiene abierta mientras alguien pida imágenes sueltas.
SNAPSHOT_LEASE_SECONDS = 6.0

# Callback de incidencias: (camera_id, evento, frame, observación) -> None
EventHandler = Callable[[int, object, object, object], None]


@dataclass
class MonitorState:
    engine: ComplianceEngine
    pipeline: object  # DetectorPipeline o un sustituto en pruebas
    on_event: EventHandler
    workshop_id: int | None = None
    started_at: float = field(default_factory=time.time)
    analyses: int = 0
    events: int = 0
    last_result: object | None = None


@dataclass
class CameraStream:
    camera_id: int
    stream_url: str
    capture: object
    is_file: bool
    source_fps: float
    draw_boxes: bool
    confidence_threshold: float
    processing_fps: float
    condition: threading.Condition = field(default_factory=threading.Condition)
    stop_event: threading.Event = field(default_factory=threading.Event)
    latest_frame: object | None = None
    latest_frame_ts: float = 0.0
    frame_sequence: int = 0
    processed_frame_sequence: int = 0
    latest_jpeg: bytes | None = None
    jpeg_sequence: int = 0
    overlay: dict | None = None  # último mensaje del motor (temporal, no se guarda)
    overlays: deque = field(default_factory=lambda: deque(maxlen=20))
    due_overlay: dict | None = None
    sync_delay: float = 0.0
    jpeg_buffer: deque = field(default_factory=deque)  # (ts, jpeg) solo en memoria
    live_jpeg: bytes | None = None  # sin retraso, para el modo "en vivo"
    reconnecting: bool = False
    live_sequence: int = 0
    subscriptions: set = field(default_factory=set)
    monitor: MonitorState | None = None
    error: str | None = None
    inference_count: int = 0
    capture_thread: threading.Thread | None = None
    inference_thread: threading.Thread | None = None

    @property
    def subscribers(self) -> int:
        return len(self.subscriptions)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def resolve_source(stream_url: str) -> str:
    """Ruta de video que funciona en cualquier PC: si la ruta guardada no existe (el proyecto
    está en otra carpeta), se busca el mismo archivo en videos/cameras o videos/ del proyecto."""
    if stream_url.isdigit() or '://' in stream_url or Path(stream_url).is_file():
        return stream_url
    name = Path(stream_url.replace('\\', '/')).name
    for folder in (PROJECT_ROOT / 'videos' / 'cameras', PROJECT_ROOT / 'videos'):
        candidate = folder / name
        if name and candidate.is_file():
            return str(candidate)
    return stream_url


def _open_capture(stream_url: str):
    from app.modules.ai_vision.browser_ingest import is_browser_source, open_browser_capture
    if is_browser_source(stream_url):
        return open_browser_capture(stream_url)
    stream_url = resolve_source(stream_url)
    is_file = Path(stream_url).is_file()
    source = stream_url if is_file or not stream_url.isdigit() else int(stream_url)
    return cv2.VideoCapture(source), is_file


class VideoStreamProcessor:
    def __init__(self, model_path='best.pt', image_size=None, jpeg_quality=75, confidence_floor=0.25,
                 pose_model_path='yolo11n-pose.pt', capture_factory=None, pipeline_factory=None):
        self.model_path = model_path
        self.pose_model_path = pose_model_path
        self.image_size = image_size or _image_size_from_env()
        self.jpeg_quality = jpeg_quality
        self.confidence_floor = confidence_floor
        self.streams: dict[int, CameraStream] = {}
        self.lock = threading.RLock()
        self.inference_lock = threading.Lock()
        self._capture_factory = capture_factory or _open_capture
        self._pipeline_factory = pipeline_factory
        self._equipment_detector = None
        self._model_lock = threading.Lock()
        self._snapshot_leases: dict[int, list] = {}  # camera_id -> [token, último uso]
        self._lease_thread: threading.Thread | None = None

    # ------------------------------------------------------------------ modelos
    @property
    def equipment_detector(self):
        """best.pt, cargado al primer uso para no bloquear el arranque."""
        with self._model_lock:
            if self._equipment_detector is None:
                from app.modules.ai_vision.detectors import EquipmentDetector
                self._equipment_detector = EquipmentDetector(self.model_path)
            return self._equipment_detector

    def _new_pipeline(self):
        if self._pipeline_factory:
            return self._pipeline_factory()
        from app.modules.ai_vision import performance
        from app.modules.ai_vision.detectors import DetectorPipeline, PersonTracker
        pipeline = DetectorPipeline(self.equipment_detector, PersonTracker(self.pose_model_path), self.image_size)
        performance.measure(pipeline)  # solo la primera vez: ajusta el límite a este equipo
        return pipeline

    def predict_options(self, confidence_threshold):
        """Argumentos de YOLO para una cámara.

        El umbral configurado en la cámara se respeta tal cual; confidence_floor
        solo se usa si la cámara no tiene un umbral válido.
        """
        return {
            'conf': self.effective_confidence(confidence_threshold),
            'imgsz': self.image_size,
            'max_det': 300,
            'verbose': False,
        }

    def effective_confidence(self, confidence_threshold) -> float:
        if confidence_threshold is None or not 0 < confidence_threshold <= 1:
            confidence_threshold = self.confidence_floor
        return float(confidence_threshold)

    @staticmethod
    def effective_fps(processing_fps) -> float:
        try:
            value = float(processing_fps)
        except (TypeError, ValueError):
            value = max_processing_fps()
        return min(max_processing_fps(), max(0.5, value))

    # ------------------------------------------------------------ suscripciones
    def subscribe(self, camera_id, stream_url, draw_boxes=True, confidence_threshold=0.5, processing_fps=10,
                  monitor=False) -> str:
        """Registra un espectador (o el monitoreo, con monitor=True) y devuelve su identificador."""
        token = f'monitor-{uuid4().hex}' if monitor else uuid4().hex
        with self.lock:
            stream = self.streams.get(camera_id)
            if stream and stream.stream_url == stream_url and not stream.stop_event.is_set():
                stream.subscriptions.add(token)
                return token

        # Abrir la fuente puede tardar segundos: se hace fuera del lock para no
        # congelar a los clientes de otras cámaras.
        capture, is_file = self._capture_factory(stream_url)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f'No se pudo abrir la fuente de video: {stream_url}')
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        source_fps = capture.get(cv2.CAP_PROP_FPS)
        if not 1 <= source_fps <= 120:
            source_fps = 25.0

        with self.lock:
            current = self.streams.get(camera_id)
            if current and current.stream_url == stream_url and not current.stop_event.is_set():
                # Otro cliente la abrió mientras tanto: se reutiliza esa.
                capture.release()
                current.subscriptions.add(token)
                return token
            if current:
                self._stop_stream(current)
            stream = CameraStream(
                camera_id=camera_id,
                stream_url=stream_url,
                capture=capture,
                is_file=is_file,
                source_fps=source_fps,
                draw_boxes=draw_boxes,
                confidence_threshold=confidence_threshold,
                processing_fps=self.effective_fps(processing_fps),
                subscriptions={token},
            )
            stream.capture_thread = threading.Thread(
                target=self._capture_loop, args=(stream,), daemon=True, name=f'camera-capture-{camera_id}',
            )
            stream.inference_thread = threading.Thread(
                target=self._inference_loop, args=(stream,), daemon=True, name=f'camera-inference-{camera_id}',
            )
            self.streams[camera_id] = stream
            stream.capture_thread.start()
            stream.inference_thread.start()
        return token

    def unsubscribe(self, camera_id, token=None):
        """Da de baja una suscripción. Una baja de un stream anterior no afecta al actual."""
        with self.lock:
            stream = self.streams.get(camera_id)
            if not stream:
                return
            if token is None:
                # Compatibilidad: sin identificador se quita una suscripción de espectador.
                viewer_tokens = [t for t in stream.subscriptions if not t.startswith('monitor-')]
                if not viewer_tokens:
                    return
                token = viewer_tokens[0]
            if token not in stream.subscriptions:
                return
            stream.subscriptions.discard(token)
            if stream.subscriptions:
                return
            self.streams.pop(camera_id, None)
            self._stop_stream(stream)

    def snapshot(self, camera_id, stream_url, live=False, timeout=3.0, **subscribe_options) -> bytes | None:
        """Última imagen JPEG de la cámara, para miniaturas consultadas 1-2 veces por segundo.

        No deja una conexión abierta en el navegador: la cámara se mantiene abierta con una
        "reserva" que caduca a los SNAPSHOT_LEASE_SECONDS sin peticiones.
        """
        with self.lock:
            lease = self._snapshot_leases.get(camera_id)
            stream = self.streams.get(camera_id)
            valid = lease and stream and lease[0] in stream.subscriptions and not stream.stop_event.is_set()
            if valid:
                lease[1] = time.monotonic()
        if not valid:
            token = self.subscribe(camera_id, stream_url, **subscribe_options)
            with self.lock:
                self._snapshot_leases[camera_id] = [token, time.monotonic()]
            self._ensure_lease_reaper()
        with self.lock:
            stream = self.streams.get(camera_id)
        if not stream:
            return None
        with stream.condition:
            if live and stream.live_jpeg is None or not live and stream.latest_jpeg is None:
                stream.condition.wait_for(
                    lambda: (stream.live_jpeg if live else stream.latest_jpeg) is not None
                    or stream.stop_event.is_set(), timeout=timeout)
            return stream.live_jpeg if live else stream.latest_jpeg

    def _ensure_lease_reaper(self):
        with self.lock:
            if self._lease_thread and self._lease_thread.is_alive():
                return
            self._lease_thread = threading.Thread(target=self._reap_leases, daemon=True, name='snapshot-leases')
            self._lease_thread.start()

    def _reap_leases(self):
        while True:
            time.sleep(1.0)
            now = time.monotonic()
            with self.lock:
                expired = [(camera_id, lease[0]) for camera_id, lease in self._snapshot_leases.items()
                           if now - lease[1] > SNAPSHOT_LEASE_SECONDS]
                for camera_id, _ in expired:
                    self._snapshot_leases.pop(camera_id, None)
                if not self._snapshot_leases and not expired:
                    self._lease_thread = None
                    return
            for camera_id, token in expired:
                self.unsubscribe(camera_id, token)

    def release_camera(self, camera_id):
        with self.lock:
            stream = self.streams.pop(camera_id, None)
            if stream:
                self._stop_stream(stream)

    # --------------------------------------------------------------- monitoreo
    def start_monitoring(self, camera_id, stream_url, rules: list[EquipmentRule], on_event: EventHandler,
                         confidence_threshold=0.5, processing_fps=None, workshop_id=None,
                         config: ComplianceConfig | None = None) -> bool:
        """Inicia el análisis continuo de una cámara. Devuelve False si ya estaba activo."""
        with self.lock:
            stream = self.streams.get(camera_id)
            if stream and stream.monitor and not stream.stop_event.is_set():
                return False
        monitor = MonitorState(ComplianceEngine(rules, config), self._new_pipeline(), on_event, workshop_id)
        self.subscribe(camera_id, stream_url, confidence_threshold=confidence_threshold,
                       processing_fps=processing_fps, monitor=True)
        with self.lock:
            stream = self.streams[camera_id]
            with stream.condition:
                stream.monitor = monitor
        return True

    def update_monitoring_rules(self, camera_id, rules: list[EquipmentRule]) -> bool:
        """Cambia las reglas de una cámara en práctica sin cortar el video. Devuelve True si cambiaron.
        La verificación de cada persona empieza de nuevo con las reglas nuevas."""
        with self.lock:
            stream = self.streams.get(camera_id)
        if not stream or not stream.monitor:
            return False
        with stream.condition:
            monitor = stream.monitor
            current = monitor.engine.rules + monitor.engine.unsupported_rules
            key = lambda items: sorted(items, key=lambda rule: rule.equipment_id)
            if key(current) == key(list(rules)):
                return False
            monitor.engine = ComplianceEngine(rules, monitor.engine.config)
        return True

    def stop_monitoring(self, camera_id) -> bool:
        with self.lock:
            stream = self.streams.get(camera_id)
            if not stream or not stream.monitor:
                return False
            with stream.condition:
                stream.monitor = None
            tokens = [t for t in stream.subscriptions if t.startswith('monitor-')]
        for token in tokens:
            self.unsubscribe(camera_id, token)
        return True

    def monitoring_status(self, camera_id) -> dict | None:
        with self.lock:
            stream = self.streams.get(camera_id)
        if not stream or not stream.monitor:
            return None
        monitor = stream.monitor
        return {
            'camera_id': camera_id,
            'workshop_id': monitor.workshop_id,
            'running': not stream.stop_event.is_set(),
            'error': stream.error,
            'analyses': monitor.analyses,
            'events': monitor.events,
            'viewers': sum(not t.startswith('monitor-') for t in stream.subscriptions),
            'started_at': monitor.started_at,
            'reconnecting': stream.reconnecting,
            # Para la barra de sincronización del frontend.
            'sync_delay': round(stream.sync_delay, 2),
            'analysis_rate': round(monitor.analyses / max(1.0, time.time() - monitor.started_at), 2),
        }

    # ----------------------------------------------------------------- frames
    def get_frame(self, camera_id, after_sequence=0, timeout=5, live=False):
        """Siguiente JPEG. live=True: sin el retraso de sincronización (recuadros atrasados)."""
        with self.lock:
            stream = self.streams.get(camera_id)
        if not stream:
            return None, [], after_sequence

        def sequence():
            return stream.live_sequence if live else stream.jpeg_sequence

        with stream.condition:
            stream.condition.wait_for(
                lambda: sequence() > after_sequence or stream.error or stream.stop_event.is_set(),
                timeout=timeout,
            )
            if sequence() <= after_sequence:
                return None, [], after_sequence
            return (stream.live_jpeg if live else stream.latest_jpeg), [], sequence()

    def _capture_loop(self, stream: CameraStream):
        frame_interval = 1 / stream.source_fps if stream.is_file else 0
        next_frame_at = time.monotonic()
        try:
            while not stream.stop_event.is_set():
                success, frame = stream.capture.read()
                if not success:
                    if stream.is_file and not stream.stop_event.is_set():
                        stream.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        next_frame_at = time.monotonic()
                        continue
                    if self._reconnect(stream):
                        next_frame_at = time.monotonic()
                        continue
                    logger.warning('La captura de cámara %s terminó sin frames.', stream.camera_id)
                    with stream.condition:
                        stream.error = 'No se pudo leer un frame de la fuente.'
                        stream.condition.notify_all()
                    break

                captured_at = time.monotonic()
                with stream.condition:
                    stream.latest_frame = frame
                    stream.latest_frame_ts = captured_at
                    stream.frame_sequence += 1
                    has_viewers = any(not t.startswith('monitor-') for t in stream.subscriptions)
                    delay = stream.sync_delay if stream.monitor else 0.0

                # Solo se comprime a JPEG si alguien está mirando. El video sale limpio:
                # los recuadros los dibuja el frontend con el mensaje del motor (Tarea 1C).
                if has_viewers:
                    encoded, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
                    if encoded:
                        self._publish_jpeg(stream, captured_at, buffer.tobytes(), delay)
                else:
                    with stream.condition:
                        stream.condition.notify_all()

                if frame_interval:
                    next_frame_at += frame_interval
                    delay = next_frame_at - time.monotonic()
                    if delay > 0:
                        stream.stop_event.wait(delay)
                    else:
                        next_frame_at = time.monotonic()
        except Exception as error:
            logger.exception('Error capturando cámara %s.', stream.camera_id)
            with stream.condition:
                stream.error = str(error)
                stream.condition.notify_all()
        finally:
            stream.capture.release()
            stream.stop_event.set()
            with stream.condition:
                stream.condition.notify_all()

    def _reconnect(self, stream: CameraStream) -> bool:
        """Reabre una cámara en vivo que dejó de dar imagen. Durante el corte no se analiza,
        así que no se generan faltas: el motor solo recibe frames reales."""
        with stream.condition:
            stream.reconnecting = True
            stream.condition.notify_all()
        logger.warning('Cámara %s sin señal: reconectando.', stream.camera_id)
        stream.capture.release()
        from app.modules.ai_vision.browser_ingest import is_browser_source
        browser = is_browser_source(stream.stream_url)
        started, wait = time.monotonic(), (0.5 if browser else RECONNECT_FIRST_WAIT)
        try:
            # Una estación del navegador puede volver en cualquier momento: se espera sin límite.
            while not stream.stop_event.is_set() and (browser or time.monotonic() - started < RECONNECT_MAX_SECONDS):
                if stream.stop_event.wait(wait):
                    return False
                wait = min(1.0 if browser else RECONNECT_MAX_WAIT, wait * 2)
                try:
                    capture, _ = self._capture_factory(stream.stream_url)
                except Exception:
                    continue
                opened = capture.slot.has_signal() if browser else capture.isOpened()
                if opened:
                    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    stream.capture = capture
                    logger.info('Cámara %s reconectada.', stream.camera_id)
                    return True
                capture.release()
            return False
        finally:
            with stream.condition:
                stream.reconnecting = False
                stream.condition.notify_all()

    def _inference_loop(self, stream: CameraStream):
        last_frame_sequence = 0
        next_inference_at = 0.0
        interval = 1 / stream.processing_fps
        while not stream.stop_event.is_set():
            if stream.monitor is None:
                # Sin práctica iniciada no se analiza: no hay alertas fuera de la práctica.
                with stream.condition:
                    stream.sync_delay, stream.due_overlay = 0.0, None
                    stream.overlays.clear()
                stream.stop_event.wait(interval)
                continue
            with stream.condition:
                ready = stream.condition.wait_for(
                    lambda: stream.stop_event.is_set() or (
                        stream.frame_sequence > last_frame_sequence
                        and time.monotonic() >= next_inference_at
                    ),
                    timeout=max(0.01, next_inference_at - time.monotonic()),
                )
                if stream.stop_event.is_set():
                    return
                if not ready or stream.latest_frame is None:
                    continue
                # Siempre el último frame: si el análisis va atrasado se descartan los intermedios.
                frame = stream.latest_frame.copy()
                frame_ts = stream.latest_frame_ts
                last_frame_sequence = stream.frame_sequence
                monitor = stream.monitor
            if monitor is None:
                continue

            try:
                overlay = self._analyze_for_monitoring(stream, monitor, frame, frame_ts)
                latency = time.monotonic() - frame_ts
                with stream.condition:
                    stream.overlay = overlay
                    stream.overlays.append(overlay)
                    # Sube rápido si el análisis tarda más; baja despacio para no dar saltos.
                    target = min(MAX_SYNC_DELAY, latency + 0.15)
                    stream.sync_delay = target if target > stream.sync_delay else 0.95 * stream.sync_delay + 0.05 * target
                    stream.inference_count += 1
                    stream.processed_frame_sequence = last_frame_sequence
                    stream.condition.notify_all()
                next_inference_at = time.monotonic() + interval
            except Exception as error:
                logger.exception('Error ejecutando inferencia para cámara %s.', stream.camera_id)
                with stream.condition:
                    stream.error = str(error)
                    stream.stop_event.set()
                    stream.condition.notify_all()
                return

    def _analyze_for_monitoring(self, stream: CameraStream, monitor: MonitorState, frame,
                                frame_ts: float | None = None) -> dict:
        frame_ts = frame_ts if frame_ts is not None else time.monotonic()
        observation = monitor.pipeline.analyze(
            frame, frame_ts, self.effective_confidence(stream.confidence_threshold),
        )
        result = monitor.engine.update(observation)
        monitor.analyses += 1
        monitor.last_result = result
        for event in result.events:
            monitor.events += 1
            try:
                monitor.on_event(stream.camera_id, event, frame, observation)
            except Exception:
                # Un fallo al guardar no debe detener el análisis de la cámara.
                logger.exception('No se pudo registrar la incidencia de la cámara %s.', stream.camera_id)
        from app.modules.reglas_lab.overlay import build_overlay_message
        message = build_overlay_message(
            stream.camera_id, monitor.analyses, time.time(), observation, result,
            monitor.engine.rules + monitor.engine.unsupported_rules,
        )
        message['frame_ts'] = frame_ts
        return message

    @staticmethod
    def _publish_jpeg(stream: CameraStream, captured_at: float, jpeg: bytes, delay: float) -> None:
        """Publica el frame para los espectadores, retrasado `delay` s durante la práctica."""
        with stream.condition:
            stream.live_jpeg = jpeg
            stream.live_sequence += 1
            if delay <= 0:
                stream.jpeg_buffer.clear()
                ready = jpeg
            else:
                stream.jpeg_buffer.append((captured_at, jpeg))
                limit = time.monotonic() - delay
                ready = None
                while stream.jpeg_buffer and stream.jpeg_buffer[0][0] <= limit:
                    ready = stream.jpeg_buffer.popleft()[1]
                # Nunca acumular más de MAX_SYNC_DELAY de video en memoria.
                while stream.jpeg_buffer and captured_at - stream.jpeg_buffer[0][0] > MAX_SYNC_DELAY:
                    ready = stream.jpeg_buffer.popleft()[1]
            if ready is not None:
                stream.latest_jpeg = ready
                stream.jpeg_sequence += 1
            stream.condition.notify_all()

    def latest_overlay(self, camera_id, live=False) -> dict | None:
        """Mensaje del motor que corresponde al frame que ven ahora los espectadores
        (el video va retrasado `sync_delay`). None si no hay práctica en curso."""
        with self.lock:
            stream = self.streams.get(camera_id)
        if not stream or not stream.monitor or stream.stop_event.is_set():
            return None
        with stream.condition:
            if live:
                return stream.overlay  # el más reciente, aunque el video vaya por delante
            limit = time.monotonic() - stream.sync_delay
            for overlay in stream.overlays:
                if overlay.get('frame_ts', 0) <= limit:
                    stream.due_overlay = overlay
            return stream.due_overlay

    def _stop_stream(self, stream: CameraStream):
        stream.stop_event.set()
        with stream.condition:
            stream.condition.notify_all()


video_processor = VideoStreamProcessor()
