"""Turnos de análisis: una sola cámara analiza a la vez y el repartidor decide cuál sigue.

Medido el 2026-11-12 en una CPU de 2 núcleos (4 hilos): con un hilo de análisis por cámara, 5 cámaras
compitiendo a la vez daban 0.36 análisis/s en total (cada cámara cada 12-25 s), menos que una cámara
sola (~1.3/s). Por turnos se recupera el rendimiento completo.

Orden de prioridad para el siguiente turno:
1. Una cámara que lleva MAX_GAP_SECONDS o más sin analizarse (la que más lleve): las alertas deben salir
   de todas las cámaras aunque nadie las mire, y el motor necesita muestras cada pocos segundos.
2. La cámara ampliada en Monitoreo (con un espectador de video continuo), como máximo 1 de cada
   FOCUS_EVERY turnos: sus recuadros se actualizan más seguido.
3. La que lleve más tiempo sin analizarse.
"""
from __future__ import annotations

import threading
import time
from collections import deque

MAX_GAP_SECONDS = 4.0
FOCUS_EVERY = 3


class AnalysisTurns:
    def __init__(self, max_gap: float = MAX_GAP_SECONDS, focus_every: int = FOCUS_EVERY, slots: int = 1):
        self.max_gap = max_gap
        self.focus_every = focus_every
        self.slots = slots  # análisis simultáneos (1 en CPU)
        self._condition = threading.Condition()
        self._waiting: dict[int, tuple[float, bool]] = {}  # cámara -> (desde cuándo espera, ampliada)
        self._running: set[int] = set()
        self._last_turn: dict[int, float] = {}
        self._recent: deque[int] = deque(maxlen=max(1, focus_every - 1))

    def set_slots(self, slots: int) -> None:
        """Análisis a la vez decididos para este equipo (performance.plan_analysis)."""
        with self._condition:
            self.slots = max(1, int(slots))
            self._condition.notify_all()

    def _since_last(self, camera_id: int, now: float, waiting_since: float) -> float:
        return now - self._last_turn.get(camera_id, waiting_since)

    def _choose(self, now: float) -> int | None:
        if not self._waiting or len(self._running) >= self.slots:
            return None
        gaps = {cid: self._since_last(cid, now, since) for cid, (since, _) in self._waiting.items()}
        overdue = [cid for cid, gap in gaps.items() if gap >= self.max_gap]
        if overdue:
            return max(overdue, key=gaps.get)
        focused = [cid for cid, (_, focus) in self._waiting.items() if focus and cid not in self._recent]
        if focused and len(self._waiting) > 1:
            return max(focused, key=gaps.get)
        return max(gaps, key=gaps.get)

    def acquire(self, camera_id: int, focused: bool, stop: threading.Event | None = None,
                timeout: float = 30.0) -> bool:
        """Espera el turno de la cámara. False si se pidió detenerla o se agotó el tiempo."""
        deadline = time.monotonic() + timeout
        with self._condition:
            since = self._waiting.get(camera_id, (time.monotonic(), focused))[0]
            self._waiting[camera_id] = (since, focused)
            try:
                while True:
                    if stop is not None and stop.is_set():
                        return False
                    if self._choose(time.monotonic()) == camera_id:
                        self._running.add(camera_id)
                        return True
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._condition.wait(min(0.25, remaining))
            finally:
                self._waiting.pop(camera_id, None)
                self._condition.notify_all()

    def release(self, camera_id: int) -> None:
        with self._condition:
            self._running.discard(camera_id)
            self._last_turn[camera_id] = time.monotonic()
            self._recent.append(camera_id)
            self._condition.notify_all()

    def forget(self, camera_id: int) -> None:
        """La cámara dejó de analizarse: no debe contar como "atrasada" si vuelve."""
        with self._condition:
            self._last_turn.pop(camera_id, None)
            self._condition.notify_all()
