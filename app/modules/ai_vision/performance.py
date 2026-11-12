"""Ajuste automático al equipo: mide cuánto tarda un análisis, fija el límite por cámara y decide
cuántos análisis corren a la vez (plan_analysis, más abajo).

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
    return {'measured': False, 'ms_per_analysis': None, 'auto_max_fps': DEFAULT_MAX_FPS, 'device': None, 'hint': None,
            'physical_cores': None, 'analysis_slots': 1, 'threads_per_analysis': None, 'slots_source': None,
            'slots_measured': None}


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


# ------------------------------------------------------------------ análisis simultáneos
# Regla (2026-11-12): con un modelo pequeño (yolo11n), un análisis no aprovecha muchos núcleos; rinde más
# repartir la CPU en varios análisis a la vez de CORES_PER_ANALYSIS núcleos cada uno. Con GPU, 2.
# La regla solo dice hasta dónde probar: SIEMPRE se mide 1, 2 y lo que diga la regla, y se elige lo que más
# rinda. Medido en un PC de 2 núcleos con 5 videos: 1 a la vez = 1.8 análisis/s en total (pausas de hasta
# 11 s por cámara); 2 a la vez = 2.6/s (máx 3.2 s); las 5 a la vez = 1.0/s. La regla sola habría dicho 1.
CORES_PER_ANALYSIS = 4
GPU_SLOTS = 2
MAX_SLOTS = 8
TUNE_SECONDS = 3.0
# Con rendimientos parecidos (dentro de este margen) se prefieren menos análisis a la vez: menos memoria
# y cada cámara se analiza con una imagen más reciente.
TUNE_TOLERANCE = 0.10


def physical_cores() -> int:
    """Núcleos físicos (los hilos lógicos engañan: 4 hilos pueden ser 2 núcleos)."""
    try:
        import psutil
        cores = psutil.cpu_count(logical=False)
        if cores:
            return int(cores)
    except Exception:
        pass
    try:
        import torch
        return max(1, int(torch.get_num_threads()))  # PyTorch usa por defecto los núcleos físicos
    except Exception:
        return max(1, (os.cpu_count() or 2) // 2)


def rule_for(cores: int, cuda: bool) -> dict:
    """Análisis a la vez y núcleos para cada uno según el equipo."""
    if cuda:
        return {'slots': GPU_SLOTS, 'threads': None}
    slots = max(1, min(MAX_SLOTS, cores // CORES_PER_ANALYSIS))
    return {'slots': slots, 'threads': max(1, cores // slots)}


def candidates_for(rule_slots: int) -> list[int]:
    """Opciones que se miden: siempre 1 y 2, y hasta lo que diga la regla."""
    return sorted({1, 2, rule_slots} | ({rule_slots // 2} if rule_slots >= 4 else set()) - {0})


def choose(throughput: dict[int, float]) -> int:
    """La opción con más análisis/s; si varias están a menos del margen, la de menos análisis a la vez."""
    best = max(throughput.values())
    return min(k for k, value in throughput.items() if value >= best * (1 - TUNE_TOLERANCE))


def _manual_slots() -> int | None:
    raw = os.environ.get('SAFEVISION_ANALISIS_SIMULTANEOS', '').strip()
    try:
        return max(1, min(MAX_SLOTS, int(raw))) if raw else None
    except ValueError:
        return None


def _set_torch_threads(threads: int | None) -> None:
    if not threads:
        return
    try:
        import torch
        torch.set_num_threads(threads)
    except Exception:
        pass


def _throughput(pipelines: list, seconds: float) -> float:
    """Análisis por segundo en total con len(pipelines) análisis a la vez (imagen gris, como measure)."""
    import numpy as np

    frame = np.full((480, 640, 3), 114, np.uint8)
    counts = [0] * len(pipelines)
    stop = threading.Event()

    def worker(index):
        pipeline, ts = pipelines[index], 0.0
        while not stop.is_set():
            ts += 1.0
            pipeline.analyze(frame, ts, 0.5)
            counts[index] += 1

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(len(pipelines))]
    started = time.perf_counter()
    [t.start() for t in threads]
    time.sleep(seconds)
    stop.set()
    [t.join() for t in threads]
    return sum(counts) / (time.perf_counter() - started)


def plan_analysis(pipeline_factory, prepare=None, cuda: bool | None = None, seconds: float = TUNE_SECONDS) -> dict:
    """Decide cuántos análisis corren a la vez en este equipo (una sola vez por proceso).

    pipeline_factory() crea un analizador independiente; prepare(k) deja listo lo compartido para k análisis
    a la vez (por ejemplo, k copias del modelo de EPP). SAFEVISION_ANALISIS_SIMULTANEOS, si está definida, manda.
    """
    with _lock:
        if _profile.get('slots_source'):
            return dict(_profile)
    import numpy as np

    if cuda is None:
        cuda = 'GPU' in (device_info()['device'] or '')
    cores = physical_cores()
    rule = rule_for(cores, cuda)
    manual = _manual_slots()
    measured = None
    if manual:
        slots, source = manual, 'manual'
        threads = None if cuda else max(1, cores // slots)
    else:
        measured = {}
        try:
            for k in candidates_for(rule['slots']):
                if prepare:
                    prepare(k)
                _set_torch_threads(None if cuda else max(1, cores // k))
                pipelines = [pipeline_factory() for _ in range(k)]
                warmup = np.full((480, 640, 3), 114, np.uint8)
                for pipeline in pipelines:  # la primera inferencia de cada modelo es más lenta
                    pipeline.analyze(warmup, 0.0, 0.5)
                measured[k] = round(_throughput(pipelines, seconds), 2)
            slots, source = choose(measured), 'medido'
        except Exception:
            logger.exception('No se pudo medir con varios análisis a la vez; se usa la regla')
            slots, source, measured = rule['slots'], 'regla', None
        threads = None if cuda else max(1, cores // slots)
    _set_torch_threads(threads)
    with _lock:
        _profile.update(physical_cores=cores, analysis_slots=slots, threads_per_analysis=threads,
                        slots_source=source, slots_measured=measured)
        logger.info('Análisis simultáneos: %s (%s; %s núcleos físicos, %s hilos cada uno, medido: %s)',
                    slots, source, cores, threads, measured)
        return dict(_profile)
