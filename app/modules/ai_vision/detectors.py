"""Detectores: convierten un frame en una FrameObservation para el motor.

- Personas, puntos clave y seguimiento: modelo de pose, una instancia por cámara
  (el seguimiento con persist=True guarda estado y no debe mezclarse entre cámaras).
- EPP: best.pt, compartido entre cámaras; su clase `person` se ignora.
"""
from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from app.modules.reglas_lab.compliance import EquipmentDetection, FrameObservation, PersonDetection, torso_for_color

# Píxel "claro": brillo (V de HSV, 0-255) alto y saturación baja, es decir blanco o gris claro.
LIGHT_MIN_VALUE = 150
LIGHT_MAX_SATURATION = 60


def torso_light(frame, person: PersonDetection) -> float | None:
    """Proporción de píxeles claros en el torso de la persona (bata blanca ~1, polo oscuro ~0).
    None si no hay puntos clave suficientes para ubicar el torso."""
    import cv2

    box = torso_for_color(person) if person.keypoints is not None else None
    if box is None:
        return None
    height, width = frame.shape[:2]
    x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
    x2, y2 = min(width, int(round(box[2]))), min(height, int(round(box[3])))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    light = (hsv[..., 2] >= LIGHT_MIN_VALUE) & (hsv[..., 1] <= LIGHT_MAX_SATURATION)
    return round(float(light.mean()), 3)


def with_torso_light(frame, persons: list[PersonDetection]) -> list[PersonDetection]:
    return [replace(person, torso_light=torso_light(frame, person)) for person in persons]

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _model_path(name: str) -> str:
    path = Path(name)
    if not path.is_absolute() and (PROJECT_ROOT / path).exists():
        path = PROJECT_ROOT / path
    return str(path)


class EquipmentDetector:
    """best.pt compartido entre cámaras. Tiene tantas copias del modelo como análisis a la vez
    (performance.plan_analysis): cada análisis toma una copia libre, así dos análisis simultáneos
    no se esperan entre sí. Con 1 copia (CPU pequeña) equivale al candado de antes."""

    def __init__(self, model_path: str = 'best.pt', copies: int = 1, loader=None):
        import queue

        self.model_path = model_path
        self._loader = loader or self._load_yolo
        self._free: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self.copies = 0
        self.model = None
        self.ensure_copies(copies)

    def _load_yolo(self):
        from ultralytics import YOLO
        return YOLO(_model_path(self.model_path))

    def ensure_copies(self, copies: int) -> None:
        """Carga copias hasta tener `copies` (nunca quita: un análisis puede estar usando una)."""
        with self._lock:
            while self.copies < copies:
                model = self._loader()
                self.model = self.model or model
                self._free.put(model)
                self.copies += 1

    def detect(self, frame, conf: float, imgsz: int) -> list[EquipmentDetection]:
        model = self._free.get()
        try:
            result = model.predict(frame, conf=conf, imgsz=imgsz, max_det=300, verbose=False)[0]
        finally:
            self._free.put(model)
        items = []
        for box in result.boxes:
            name = model.names[int(box.cls[0])]
            if name != 'person':
                items.append(EquipmentDetection(name, tuple(box.xyxy[0].tolist()), float(box.conf[0])))
        return items


class PersonTracker:
    """Modelo de pose con seguimiento propio de una cámara."""

    def __init__(self, model_path: str = 'yolo11n-pose.pt'):
        from ultralytics import YOLO

        self.model = YOLO(_model_path(model_path))

    def track(self, frame, conf: float, imgsz: int) -> list[PersonDetection]:
        result = self.model.track(
            frame, persist=True, conf=conf, imgsz=imgsz, classes=[0], tracker='bytetrack.yaml', verbose=False,
        )[0]
        keypoints = result.keypoints.data.tolist() if result.keypoints is not None else None
        persons = []
        for row, box in enumerate(result.boxes):
            if box.id is None:
                continue
            persons.append(PersonDetection(
                int(box.id[0]), tuple(box.xyxy[0].tolist()), float(box.conf[0]),
                keypoints=tuple(tuple(point) for point in keypoints[row]) if keypoints else None,
            ))
        return persons


class DetectorPipeline:
    """Analiza un frame con todos los detectores y entrega una sola observación."""

    def __init__(self, equipment: EquipmentDetector, persons: PersonTracker, imgsz: int = 640,
                 person_conf: float = 0.25):
        self.equipment = equipment
        self.persons = persons
        self.imgsz = imgsz
        self.person_conf = person_conf

    def analyze(self, frame, timestamp: float, equipment_conf: float) -> FrameObservation:
        persons = with_torso_light(frame, self.persons.track(frame, self.person_conf, self.imgsz))
        equipment = self.equipment.detect(frame, equipment_conf, self.imgsz)
        height, width = frame.shape[:2]
        return FrameObservation(timestamp, persons, equipment, (width, height))
