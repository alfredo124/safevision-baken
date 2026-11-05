"""Avisos de cambios para que todas las pantallas abiertas se actualicen solas.

Cada cambio sube un número de versión por tema ('cameras', 'workshops', 'practica', 'incidents').
El WebSocket /ws/events lo consulta en memoria y avisa a los navegadores conectados; estos vuelven
a pedir los datos con su permiso habitual (el aviso no lleva datos, solo el tema).
"""
from __future__ import annotations

import re
import threading

TOPICS = ('cameras', 'workshops', 'practica', 'incidents')

_lock = threading.Lock()
_versions: dict[str, int] = {topic: 0 for topic in TOPICS}

# Peticiones que modifican algo (POST/PUT/PATCH/DELETE con respuesta 2xx) -> temas afectados.
_RULES = [
    (re.compile(r'^/modules/ai_vision/cameras(/|$)'), ('cameras',)),
    (re.compile(r'^/modules/reglas_lab/workshops/\d+/monitoring/(start|stop)$'), ('practica', 'incidents')),
    (re.compile(r'^/modules/reglas_lab/workshops(/|$)'), ('workshops', 'cameras')),
    (re.compile(r'^/modules/reglas_lab/incidents(/|$)'), ('incidents',)),
]


def notify(*topics: str) -> None:
    with _lock:
        for topic in topics:
            if topic in _versions:
                _versions[topic] += 1


def versions() -> dict[str, int]:
    with _lock:
        return dict(_versions)


def topics_for_request(method: str, path: str, status_code: int) -> tuple[str, ...]:
    if method in ('GET', 'HEAD', 'OPTIONS') or not 200 <= status_code < 300:
        return ()
    for pattern, topics in _RULES:
        if pattern.search(path):
            return topics
    return ()
