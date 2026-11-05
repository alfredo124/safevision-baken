"""Detectores: convierten un frame en una FrameObservation para el motor.

- Personas, puntos clave y seguimiento: modelo de pose, una instancia por cámara
  (el seguimiento con persist=True guarda estado y no debe mezclarse entre cámaras).
- EPP: best.pt, compartido entre cámaras; su clase `person` se ignora.
"""
from __future__ import annotations

import threading
from pathlib import Path

from app.modules.reglas_lab.compliance import EquipmentDetection, FrameObservation, PersonDetection

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _model_path(name: str) -> str:
    path = Path(name)
    if not path.is_absolute() and (PROJECT_ROOT / path).exists():
        path = PROJECT_ROOT / path
    return str(path)


class EquipmentDetector:
    """best.pt compartido. Un lock evita inferencias simultáneas sobre el mismo modelo."""

    def __init__(self, model_path: str = 'best.pt'):
        from ultralytics import YOLO

        self.model = YOLO(_model_path(model_path))
        self.lock = threading.Lock()

    def detect(self, frame, conf: float, imgsz: int) -> list[EquipmentDetection]:
        with self.lock:
            result = self.model.predict(frame, conf=conf, imgsz=imgsz, max_det=300, verbose=False)[0]
        items = []
        for box in result.boxes:
            name = self.model.names[int(box.cls[0])]
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
        persons = self.persons.track(frame, self.person_conf, self.imgsz)
        equipment = self.equipment.detect(frame, equipment_conf, self.imgsz)
        height, width = frame.shape[:2]
        return FrameObservation(timestamp, persons, equipment, (width, height))
