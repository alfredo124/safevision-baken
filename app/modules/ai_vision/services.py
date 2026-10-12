import os
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
from ultralytics import YOLO

logger = logging.getLogger(__name__)


@dataclass
class CameraStream:
    camera_id: int
    stream_url: str
    capture: cv2.VideoCapture
    is_file: bool
    source_fps: float
    draw_boxes: bool
    confidence_threshold: float
    processing_fps: float
    condition: threading.Condition = field(default_factory=threading.Condition)
    stop_event: threading.Event = field(default_factory=threading.Event)
    latest_frame: object | None = None
    frame_sequence: int = 0
    processed_frame_sequence: int = 0
    latest_jpeg: bytes | None = None
    jpeg_sequence: int = 0
    detections: list = field(default_factory=list)
    detections_updated_at: float = 0.0
    subscribers: int = 0
    error: str | None = None
    inference_count: int = 0
    capture_thread: threading.Thread | None = None
    inference_thread: threading.Thread | None = None


class VideoStreamProcessor:
    def __init__(self, model_path='best.pt', image_size=512, jpeg_quality=75, confidence_floor=0.25):
        project_root = Path(__file__).resolve().parents[3]
        model_file = Path(model_path)
        if not model_file.is_absolute():
            model_file = project_root / model_file
        self.model = YOLO(str(model_file))
        self.image_size = image_size
        self.jpeg_quality = jpeg_quality
        self.confidence_floor = confidence_floor
        self.streams: dict[int, CameraStream] = {}
        self.lock = threading.RLock()
        self.inference_lock = threading.Lock()
        self.latest_detections: dict[int, list] = {}

    def subscribe(self, camera_id, stream_url, draw_boxes=True, confidence_threshold=0.5, processing_fps=10):
        with self.lock:
            stream = self.streams.get(camera_id)
            if stream and stream.stream_url == stream_url and not stream.stop_event.is_set():
                stream.subscribers += 1
                return
            if stream:
                self._stop_stream(stream)

            is_file = Path(stream_url).is_file()
            source = stream_url if is_file or not stream_url.isdigit() else int(stream_url)
            capture = cv2.VideoCapture(source)
            if not capture.isOpened():
                capture.release()
                raise RuntimeError(f'No se pudo abrir la fuente de video: {stream_url}')
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            source_fps = capture.get(cv2.CAP_PROP_FPS)
            if not 1 <= source_fps <= 120:
                source_fps = 25.0

            stream = CameraStream(
                camera_id=camera_id,
                stream_url=stream_url,
                capture=capture,
                is_file=is_file,
                source_fps=source_fps,
                draw_boxes=draw_boxes,
                confidence_threshold=confidence_threshold,
                processing_fps=min(2, max(1, processing_fps)),
                subscribers=1,
            )
            stream.capture_thread = threading.Thread(
                target=self._capture_loop, args=(stream,), daemon=True,
                name=f'camera-capture-{camera_id}',
            )
            stream.inference_thread = threading.Thread(
                target=self._inference_loop, args=(stream,), daemon=True,
                name=f'camera-inference-{camera_id}',
            )
            self.streams[camera_id] = stream
            stream.capture_thread.start()
            stream.inference_thread.start()

    def get_frame(self, camera_id, after_sequence=0, timeout=5):
        with self.lock:
            stream = self.streams.get(camera_id)
        if not stream:
            return None, [], after_sequence

        with stream.condition:
            stream.condition.wait_for(
                lambda: stream.jpeg_sequence > after_sequence or stream.error or stream.stop_event.is_set(),
                timeout=timeout,
            )
            if stream.jpeg_sequence <= after_sequence:
                return None, stream.detections, after_sequence
            return stream.latest_jpeg, stream.detections, stream.jpeg_sequence

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
                    logger.warning('La captura de cámara %s terminó sin frames.', stream.camera_id)
                    with stream.condition:
                        stream.error = 'No se pudo leer un frame de la fuente.'
                        stream.condition.notify_all()
                    break

                with stream.condition:
                    stream.latest_frame = frame
                    stream.frame_sequence += 1
                    detections = list(stream.detections)
                    detection_age = time.monotonic() - stream.detections_updated_at
                    if detections and detection_age > 1.0:
                        detections = []
                        stream.detections = []
                        self.latest_detections[stream.camera_id] = []

                output_frame = frame.copy()
                if stream.draw_boxes:
                    self._draw_detections(output_frame, detections)
                encoded, buffer = cv2.imencode(
                    '.jpg', output_frame,
                    [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
                )
                if encoded:
                    with stream.condition:
                        stream.latest_jpeg = buffer.tobytes()
                        stream.jpeg_sequence += 1
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

    def _inference_loop(self, stream: CameraStream):
        last_frame_sequence = 0
        next_inference_at = 0.0
        interval = 1 / stream.processing_fps
        while not stream.stop_event.is_set():
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

            try:
                with self.inference_lock:
                    with stream.condition:
                        frame = stream.latest_frame.copy()
                        last_frame_sequence = stream.frame_sequence
                    result = self.model.predict(
                        frame,
                        conf=min(stream.confidence_threshold, self.confidence_floor),
                        imgsz=self.image_size,
                        vid_stride=1,
                        max_det=300,
                        verbose=False,
                    )[0]
                detections = []
                for box in result.boxes:
                    class_id = int(box.cls[0])
                    detections.append({
                        'box': box.xyxy[0].tolist(),
                        'conf': float(box.conf[0]),
                        'class': class_id,
                        'name': self.model.names[class_id],
                    })
                with stream.condition:
                    stream.detections = detections
                    stream.detections_updated_at = time.monotonic()
                    stream.inference_count += 1
                    stream.processed_frame_sequence = last_frame_sequence
                    self.latest_detections[stream.camera_id] = detections
                    stream.condition.notify_all()
                next_inference_at = time.monotonic() + interval
            except Exception as error:
                logger.exception('Error ejecutando inferencia para cámara %s.', stream.camera_id)
                with stream.condition:
                    stream.error = str(error)
                    stream.stop_event.set()
                    stream.condition.notify_all()
                return

    @staticmethod
    def _draw_detections(frame, detections):
        for detection in detections:
            x1, y1, x2, y2 = map(int, detection['box'])
            label = f"{detection['name']} {detection['conf']:.2f}"
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 0), 2)
            cv2.putText(frame, label, (x1, max(y1 - 8, 0)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (0, 200, 0), 2)

    def _stop_stream(self, stream: CameraStream):
        stream.stop_event.set()
        with stream.condition:
            stream.condition.notify_all()
        stream.capture.release()

    def unsubscribe(self, camera_id):
        with self.lock:
            stream = self.streams.get(camera_id)
            if not stream:
                return
            stream.subscribers -= 1
            if stream.subscribers > 0:
                return
            self.streams.pop(camera_id, None)
            self.latest_detections.pop(camera_id, None)
            self._stop_stream(stream)

    def release_camera(self, camera_id):
        with self.lock:
            stream = self.streams.pop(camera_id, None)
            self.latest_detections.pop(camera_id, None)
            if stream:
                self._stop_stream(stream)


video_processor = VideoStreamProcessor()
