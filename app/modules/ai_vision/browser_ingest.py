"""Cámaras del navegador (Tarea 0C).

Una "estación de cámara" (navegador con getUserMedia) envía JPEG por WebSocket. Cada frame
se guarda solo en memoria, en una ranura por cámara, y BrowserCapture la expone con la misma
interfaz que cv2.VideoCapture. Así el análisis, las miniaturas, los recuadros y las alertas
funcionan igual que con una webcam o una cámara RTSP. Nada se escribe en disco.
"""
from __future__ import annotations

import threading
import time

import cv2

BROWSER_PREFIX = 'browser:'
# Sin frames durante este tiempo, la cámara se considera sin señal (no se analiza).
FRAME_TIMEOUT = 5.0
# Ritmo máximo aceptado desde una estación; los frames más seguidos se descartan. La estación
# envía hasta 15/s (fluidez del video en Monitoreo); 20 deja margen al desfase de los temporizadores.
MAX_INGEST_FPS = 20.0
MAX_FRAME_BYTES = 2 * 1024 * 1024


def is_browser_source(stream_url: str) -> bool:
    return stream_url.startswith(BROWSER_PREFIX)


class BrowserFrameSlot:
    """Último frame recibido de una estación. Solo una estación por cámara a la vez."""

    def __init__(self):
        self.condition = threading.Condition()
        self.frame = None
        self.sequence = 0
        self.received_at = 0.0
        self.station_connected = False
        self.owner = None  # conexión que transmite ahora (una sola por cámara)
        self.active_at = 0.0  # último mensaje de esa conexión
        self.accepted = 0
        self.dropped = 0

    def busy(self) -> bool:
        """Otra estación transmite de verdad. Una conexión que lleva FRAME_TIMEOUT segundos sin enviar nada
        (p. ej. el celular perdió la red y el servidor aún no lo notó) no bloquea la reconexión."""
        with self.condition:
            return self.station_connected and time.monotonic() - self.active_at < FRAME_TIMEOUT

    def claim(self):
        with self.condition:
            owner = object()
            self.owner, self.station_connected, self.active_at = owner, True, time.monotonic()
            return owner

    def touch(self, owner) -> bool:
        """Registra actividad; False si esta conexión fue reemplazada por otra."""
        with self.condition:
            if self.owner is not owner:
                return False
            self.active_at = time.monotonic()
            return True

    def release(self, owner) -> None:
        with self.condition:
            if self.owner is owner:
                self.owner, self.station_connected = None, False

    def push(self, frame) -> bool:
        """Guarda el frame. Devuelve False si llegó demasiado pronto y se descartó."""
        now = time.monotonic()
        with self.condition:
            if self.received_at and now - self.received_at < 1.0 / MAX_INGEST_FPS:
                self.dropped += 1
                return False
            self.frame = frame
            self.sequence += 1
            self.received_at = now
            self.accepted += 1
            self.condition.notify_all()
            return True

    def has_signal(self) -> bool:
        with self.condition:
            return self.station_connected and time.monotonic() - self.received_at < FRAME_TIMEOUT


_slots: dict[str, BrowserFrameSlot] = {}
_slots_lock = threading.Lock()


def get_slot(stream_url: str) -> BrowserFrameSlot:
    with _slots_lock:
        return _slots.setdefault(stream_url, BrowserFrameSlot())


def station_busy(stream_url: str) -> bool:
    """Para la lista de cámaras: hay una estación transmitiendo esta cámara ahora."""
    with _slots_lock:
        slot = _slots.get(stream_url)
    return bool(slot and slot.busy())


class BrowserCapture:
    """Interfaz de cv2.VideoCapture sobre la ranura de una estación de cámara."""

    def __init__(self, slot: BrowserFrameSlot, timeout: float | None = None):
        self.slot = slot
        self.timeout = timeout  # None: FRAME_TIMEOUT vigente
        with slot.condition:
            self.last_sequence = slot.sequence if slot.frame is None else slot.sequence - 1

    def isOpened(self) -> bool:  # noqa: N802 (misma interfaz que OpenCV)
        # Siempre "abierta": la práctica puede iniciarse antes de que la estación transmita.
        # Sin frames, read() falla y el backend queda en "reconectando" (sin análisis).
        return True

    def read(self):
        with self.slot.condition:
            ready = self.slot.condition.wait_for(lambda: self.slot.sequence > self.last_sequence,
                                                 timeout=self.timeout or FRAME_TIMEOUT)
            if not ready or self.slot.frame is None:
                return False, None
            self.last_sequence = self.slot.sequence
            return True, self.slot.frame

    def get(self, prop):
        return 0.0  # sin FPS propios: se usa el ritmo de llegada

    def set(self, *args):
        return True

    def release(self):
        pass


def open_browser_capture(stream_url: str):
    return BrowserCapture(get_slot(stream_url)), False


def decode_jpeg(data: bytes):
    import numpy as np
    if not data or len(data) > MAX_FRAME_BYTES:
        return None
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
