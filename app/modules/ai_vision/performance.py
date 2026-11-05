"""Ajuste automático al equipo: mide cuánto tarda un análisis y fija el límite por cámara.

La primera vez que se crea un análisis real (al iniciar la primera práctica) se cronometran 3
análisis sobre una imagen gris. Con ese tiempo:
  - PC lento (~0,5 s por análisis, como un portátil de 2 núcleos): 2 análisis/s por cámara.
  - PC con CPU potente: 3 o 4.   - Con GPU: 5.
SAFEVISION_MAX_FPS, si está definida, manda sobre lo automático. El tamaño de imagen no se cambia
solo: afecta a la detección y se decide con la calibración.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time

logger = logging.getLogger(__name__)

DEFAULT_MAX_FPS = 2.0
MIN_AUTO_FPS, MAX_AUTO_FPS = 2.0, 5.0
# Se deja margen (60 %) para la captura, la codificación JPEG y otras cámaras.
USAGE_SHARE = 0.6

_lock = threading.Lock()
_profile: dict = {}


def _empty_profile() -> dict:
    return {'measured': False, 'ms_per_analysis': None, 'auto_max_fps': DEFAULT_MAX_FPS, 'device': None, 'hint': None}


_profile.update(_empty_profile())


def auto_max_fps_for(ms_per_analysis: float | None) -> float:
    if not ms_per_analysis or ms_per_analysis <= 0:
        return DEFAULT_MAX_FPS
    value = round(USAGE_SHARE * 1000 / ms_per_analysis)
    return float(min(MAX_AUTO_FPS, max(MIN_AUTO_FPS, value)))


def device_info() -> dict:
    try:
        import torch
        cuda = torch.cuda.is_available()
        name = torch.cuda.get_device_name(0) if cuda else None
    except Exception:  # sin torch (pruebas) o sin controlador
        cuda, name = False, None
    hint = None
    if not cuda and shutil.which('nvidia-smi'):
        hint = ('Este PC tiene una GPU NVIDIA, pero PyTorch es la versión para CPU: instala la de CUDA '
                '(README, "PC con GPU NVIDIA") para analizar mucho más rápido.')
    return {'device': f'GPU {name}' if cuda else f'CPU ({os.cpu_count()} hilos)', 'hint': hint}


def measure(pipeline, runs: int = 3) -> dict:
    """Cronometra el análisis una sola vez por proceso. Devuelve el perfil."""
    import numpy as np

    with _lock:
        if _profile['measured']:
            return dict(_profile)
        frame = np.full((480, 640, 3), 114, np.uint8)
        ms = None
        try:
            pipeline.analyze(frame, 0.0, 0.5)  # calentamiento (primera inferencia más lenta)
            started = time.perf_counter()
            for index in range(runs):
                pipeline.analyze(frame, float(index + 1), 0.5)
            ms = (time.perf_counter() - started) * 1000 / runs
        except Exception:
            logger.exception('No se pudo medir el rendimiento; se usa el límite por defecto')
        _profile.update(measured=True, ms_per_analysis=round(ms) if ms else None,
                        auto_max_fps=auto_max_fps_for(ms), **device_info())
        logger.info('Rendimiento: %s ms por análisis en %s -> hasta %s análisis/s por cámara',
                    _profile['ms_per_analysis'], _profile['device'], _profile['auto_max_fps'])
        return dict(_profile)


def profile() -> dict:
    with _lock:
        return dict(_profile)


def reset() -> None:
    """Solo para pruebas."""
    with _lock:
        _profile.clear()
        _profile.update(_empty_profile())
