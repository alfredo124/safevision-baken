"""Captura de incidencias con la cabeza enmascarada.

Fase 1: se cubre la cabeza de TODAS las personas detectadas usando los puntos de
la cara de la pose; si no hay puntos fiables, se cubre la parte superior de la caja.
El detector de rostros llega con la Tarea 2B. El frame original nunca se escribe en
disco: solo la versión enmascarada, y solo cuando se crea una incidencia.
"""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

from app.modules.reglas_lab.compliance import PersonDetection

PROJECT_ROOT = Path(__file__).resolve().parents[3]
# SAFEVISION_CAPTURE_DIR permite otra carpeta. Obligatorio al probar con una COPIA de la base:
# si la copia y la base real comparten carpeta, borrar en la copia borra capturas reales.
CAPTURE_DIR = Path(os.environ.get('SAFEVISION_CAPTURE_DIR') or PROJECT_ROOT / 'capturas')
HEAD_KEYPOINTS = range(5)  # nariz, ojos y orejas (COCO)


def head_region(person: PersonDetection, frame_shape, min_keypoint_conf: float = 0.3) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = person.box
    height = max(1.0, y2 - y1)
    points = [
        (kp[0], kp[1]) for index, kp in enumerate(person.keypoints or ())
        if index in HEAD_KEYPOINTS and kp[2] >= min_keypoint_conf
    ]
    if points:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        size = max(max(xs) - min(xs), 0.12 * height)
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        hx1, hx2 = cx - 1.1 * size, cx + 1.1 * size
        hy1, hy2 = cy - 1.4 * size, cy + 1.2 * size
        # Por seguridad, la máscara siempre incluye además la franja superior de la caja.
        hy1 = min(hy1, y1)
    else:
        hx1, hx2, hy1, hy2 = x1, x2, y1, y1 + 0.3 * height
    frame_h, frame_w = frame_shape[:2]
    return (int(max(0, hx1)), int(max(0, hy1)), int(min(frame_w, hx2)), int(min(frame_h, hy2)))


def mask_heads(frame, persons) -> np.ndarray:
    """Devuelve una copia del frame con la cabeza de cada persona cubierta."""
    masked = frame.copy()
    for person in persons:
        x1, y1, x2, y2 = head_region(person, frame.shape)
        if x2 > x1 and y2 > y1:
            masked[y1:y2, x1:x2] = 40  # bloque sólido: no se puede revertir como un desenfoque
    return masked


def delete_capture(relative_path: str, directory: Path | None = None) -> bool:
    """Borra una captura, solo si está dentro de la carpeta de capturas."""
    directory = (directory or CAPTURE_DIR).resolve()
    path = (PROJECT_ROOT / relative_path).resolve()
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def save_incident_capture(frame, persons, incident_code: str, highlight_box=None,
                          directory: Path | None = None) -> str:
    """Escribe la captura enmascarada y devuelve su ruta relativa a la raíz del proyecto."""
    directory = directory or CAPTURE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    image = mask_heads(frame, persons)
    if highlight_box:
        x1, y1, x2, y2 = map(int, highlight_box)
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 255), 2)
    path = directory / f'{incident_code}.jpg'
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 85]):
        raise OSError(f'No se pudo escribir la captura {path}')
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)
