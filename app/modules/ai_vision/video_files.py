"""Videos subidos como cámara: se guardan a 15 fps y como máximo 640 px de lado.

Medido el 2026-11-11 con 4 cámaras de archivo en CPU (4 núcleos): con los videos originales
(25-60 fps, hasta 960x720) leerlos en tiempo real ocupa cerca de la mitad de la CPU y deja a
YOLO sin núcleos libres. PyTorch reparte cada análisis entre todos los núcleos y espera al más
lento, así que cada análisis pasó de ~0,5 s a 20-70 s: 0,01 análisis/s por cámara y ninguna
alerta. Con los videos a 15 fps y 640 px: 0,42 análisis/s por cámara, video en tiempo real y
alertas normales. El análisis usa ~2 imágenes/s y la vista ~6-15, así que no se pierde nada.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2

logger = logging.getLogger(__name__)

MAX_FPS = 15.0
MAX_SIDE = 640


def video_info(path: Path) -> tuple[float, int, int] | None:
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            return None
        return capture.get(cv2.CAP_PROP_FPS) or 0.0, int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        capture.release()


def needs_optimization(path: Path) -> bool:
    info = video_info(path)
    if info is None:
        return False
    fps, width, height = info
    return fps > MAX_FPS + 0.5 or max(width, height) > MAX_SIDE


def optimize_video(source: Path, target: Path) -> bool:
    """Escribe en `target` el video a <= 15 fps y <= 640 px. Devuelve False si no se pudo leer
    o escribir (el llamador conserva entonces el original)."""
    info = video_info(source)
    if info is None:
        return False
    fps, width, height = info
    if not 1 <= fps <= 240 or width <= 0 or height <= 0:
        return False
    step = max(1, round(fps / MAX_FPS))
    scale = min(1.0, MAX_SIDE / max(width, height))
    size = (max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2))
    capture = cv2.VideoCapture(str(source))
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*'mp4v'), fps / step, size)
    written = 0
    try:
        if not writer.isOpened():
            return False
        index = 0
        # grab() avanza sin convertir la imagen; solo se convierten los frames que se guardan.
        while capture.grab():
            if index % step == 0:
                ok, frame = capture.retrieve()
                if ok:
                    writer.write(frame if scale == 1.0 else cv2.resize(frame, size, interpolation=cv2.INTER_AREA))
                    written += 1
            index += 1
    finally:
        capture.release()
        writer.release()
    if written == 0:
        target.unlink(missing_ok=True)
        return False
    logger.info('Video optimizado: %s (%.0f fps %dx%d) -> %.0f fps %dx%d, %d frames',
                source.name, fps, width, height, fps / step, size[0], size[1], written)
    return True
