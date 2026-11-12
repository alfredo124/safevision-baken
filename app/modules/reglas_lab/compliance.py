"""Motor de cumplimiento de EPP.

Convierte detecciones por frame en estados por persona y en eventos de
infracción. No depende de YOLO, OpenCV, FastAPI ni de la base de datos, de modo
que se puede probar en segundos con detecciones grabadas o simuladas.

Principio clave: "no vi el EPP" no significa "no lleva el EPP". Un fallo aislado
del modelo o una zona del cuerpo no visible no deben generar una alerta.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Sequence

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 en píxeles

SLOT_HEAD = {'ojos', 'cabeza'}
SLOT_HANDS = {'manos'}
SLOT_BODY = {'cuerpo'}

# Puntos clave COCO de un modelo de pose (yolo11n-pose): (x, y, confianza).
Keypoint = tuple[float, float, float]
KP_NOSE, KP_LEFT_EYE, KP_RIGHT_EYE = 0, 1, 2
KP_LEFT_SHOULDER, KP_RIGHT_SHOULDER = 5, 6
KP_LEFT_WRIST, KP_RIGHT_WRIST = 9, 10
KP_LEFT_HIP, KP_RIGHT_HIP = 11, 12
FACE_KEYPOINTS = (KP_NOSE, KP_LEFT_EYE, KP_RIGHT_EYE)
WRIST_KEYPOINTS = (KP_LEFT_WRIST, KP_RIGHT_WRIST)
SHOULDER_KEYPOINTS = (KP_LEFT_SHOULDER, KP_RIGHT_SHOULDER)
HIP_KEYPOINTS = (KP_LEFT_HIP, KP_RIGHT_HIP)


class ComplianceState(str, Enum):
    COMPLIANT = 'CUMPLE'
    VIOLATION = 'INCUMPLE'
    NOT_VERIFIABLE = 'NO_VERIFICABLE'
    # Solo para mostrar: el motor acumula evidencia de una falta que aún no confirma.
    AT_RISK = 'EN_RIESGO'


class _Sample(Enum):
    PRESENT = 'present'
    ABSENT = 'absent'
    UNKNOWN = 'unknown'
    # EPP detectado pero mal puesto (bata abierta). Para el cumplimiento cuenta como
    # no presente; al confirmar la falta decide si es leve (mal puesto) o grave (ausente).
    PARTIAL = 'partial'


# Detalle de una falta cuando el EPP está, pero mal puesto (falta leve: se corrige en el momento).
DETAIL_COAT_OPEN = 'BATA_ABIERTA'
DETAIL_GOGGLES_OFF = 'LENTES_MAL_PUESTOS'  # lentes en la frente, el cuello o la mano, no en los ojos


@dataclass(frozen=True)
class EquipmentRule:
    """Regla de un taller. Equivale a SafetyEquipment + WorkshopEquipmentRule."""
    equipment_id: str
    slot: str
    model_class: str | None
    severity: str = 'MEDIO'
    rule_code: str | None = None


@dataclass(frozen=True)
class EquipmentDetection:
    name: str
    box: Box
    confidence: float


@dataclass(frozen=True)
class PersonDetection:
    track_id: int
    box: Box
    confidence: float = 1.0
    # Zonas visibles según una fuente externa, por ejemplo un modelo de pose.
    # None significa que solo se usan las comprobaciones geométricas básicas.
    visible_slots: frozenset[str] | None = None
    # Puntos clave de pose. Si existen, la visibilidad y la asociación del EPP
    # se calculan con coordenadas reales del cuerpo en lugar de zonas de la caja.
    keypoints: tuple[Keypoint, ...] | None = None
    # Proporción de píxeles claros y poco saturados (blanco o gris claro) en el torso, calculada por el
    # detector con la imagen (detectors.torso_light). None = sin dato: se usa la confianza del modelo.
    torso_light: float | None = None


@dataclass(frozen=True)
class FrameObservation:
    timestamp: float  # segundos, monótono dentro de cada cámara
    persons: Sequence[PersonDetection]
    equipment: Sequence[EquipmentDetection]
    frame_size: tuple[int, int] | None = None  # ancho, alto


@dataclass(frozen=True)
class ComplianceConfig:
    # Periodo provisional decidido con el usuario (2026-11-05): 10 s de ventana y 8 s
    # verificados, probado solo con videos editados de YouTube. Recalibrar con el
    # video propio de cámara fija.
    # 12 s desde el 2026-11-12 (antes 10): con 10 s de ventana y 8 s verificados, una cámara analizada
    # cada ~3-3.5 s (varias cámaras por turnos) nunca juntaba 8 s de muestras dentro de la ventana y no
    # alertaba nunca. Con 12 s, los videos etiquetados dan las mismas faltas de 2 análisis/s a uno cada 4.5 s.
    window_seconds: float = 12.0
    min_verified_seconds: float = 8.0
    # Con varias cámaras en CPU cada una se analiza ~0.3 veces/s: 3 muestras en la ventana
    # es lo que se consigue en 10 s. Más muestras impedirían alertar.
    min_verified_samples: int = 3
    violation_max_present_ratio: float = 0.1
    compliant_min_present_ratio: float = 0.5
    min_person_confidence: float = 0.4
    min_equipment_confidence: float = 0.25
    min_person_height_px: float = 80.0
    edge_margin_px: float = 2.0
    track_ttl_seconds: float = 3.0
    cooldown_seconds: float = 60.0
    same_person_iou: float = 0.3
    # Con pose. Distancias relativas a la altura de la persona. Calibrar con videos.
    min_keypoint_confidence: float = 0.5
    glove_max_wrist_distance: float = 0.08
    goggles_max_face_distance: float = 0.06
    # Bata cerrada (abotonada): confianza y cobertura del torso altas. Calibrado el
    # 2026-11-10 con los videos de cámara fija de los compañeros: cerrada 146/148
    # muestras; sin bata 6/115 y abierta 7/81 superan ambos umbrales.
    min_coat_torso_coverage: float = 0.8
    coat_closed_min_confidence: float = 0.7
    # Bata detectada que no cumple lo anterior = abierta, si cubre algo del torso.
    coat_open_min_coverage: float = 0.2
    # Lentes detectados cerca de la persona (hasta esta distancia de la cara, relativa a su
    # altura: frente, cabeza, cuello, mano) pero no sobre los ojos = lentes mal puestos.
    goggles_misplaced_max_distance: float = 0.45
    # Al confirmar la falta: si al menos esta proporción de las muestras sin el EPP bien puesto
    # lo vio mal puesto (bata abierta, lentes fuera de los ojos), la falta es leve; si no, es grave
    # (sin el EPP). Sin bata, ~53 % de los frames no detecta nada.
    # 0.5 desde el 2026-11-12 (antes 0.8): una bata vista abierta casi siempre, con un par de fallos
    # sueltos de detección, acababa como "falta bata" grave. En los videos etiquetados del equipo
    # 0.5 y 0.8 dan las mismas faltas y gravedades; en video de stock, 3 de 5 graves pasan a leves.
    misplaced_min_ratio: float = 0.5
    misplaced_severity: str = 'MEDIO'
    # Sin caderas visibles se evalúa la parte alta del torso (0 = desactivado). Calibrar.
    coat_upper_torso_ratio: float = 0.8
    # Color de la bata (con torso_light). Medido el 2026-11-12, proporción de torso claro p10-p90:
    # sin bata (polo oscuro) 0.00-0.04, bata abierta 0.48-0.85, bata cerrada 0.86-1.00 (videos del
    # equipo) y bata en video de stock 0.78-1.00. El modelo, en cambio, daba la misma confianza
    # (0.3-0.7) a un polo oscuro que a una bata de otra cámara: con el umbral de confianza las batas de
    # otras cámaras salían "abiertas" o "faltantes".
    # Menos torso claro que esto: la "bata" detectada es otra prenda y se ignora.
    coat_min_light: float = 0.3
    # Torso cubierto y al menos así de claro: bata puesta y cerrada, sin mirar la confianza del modelo.
    coat_closed_min_light: float = 0.8
    # Con pose, una persona solo se evalúa si se ve al menos un hombro. Medido el 2026-11-12: en los
    # videos de stock el 11-14 % de las "personas" eran manos o caras sueltas (primeros planos) que
    # generaban faltas de guantes y lentes; en los videos de cámara fija del equipo, 0 %.
    require_shoulder: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.violation_max_present_ratio < self.compliant_min_present_ratio <= 1:
            raise ValueError('Las proporciones de presencia no son coherentes')
        if not 0 < self.min_verified_seconds <= self.window_seconds:
            raise ValueError('min_verified_seconds debe estar dentro de window_seconds')


@dataclass(frozen=True)
class ViolationEvent:
    track_id: int
    equipment_id: str
    model_class: str
    severity: str
    timestamp: float
    started_at: float
    person_box: Box
    present_ratio: float
    rule_code: str | None = None
    # None = el EPP falta; DETAIL_COAT_OPEN / DETAIL_GOGGLES_OFF = está, pero mal puesto.
    detail: str | None = None


@dataclass(frozen=True)
class PersonEquipmentStatus:
    track_id: int
    equipment_id: str
    state: ComplianceState
    present_ratio: float | None
    progress: float = 0.0  # 0..1 hacia la confirmación de la falta (EN_RIESGO / INCUMPLE)
    # Solo con INCUMPLE: gravedad de la falta (puede ser menor que la de la regla) y detalle.
    severity: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ComplianceResult:
    timestamp: float
    statuses: tuple[PersonEquipmentStatus, ...]
    events: tuple[ViolationEvent, ...]


@dataclass
class _RuleTrack:
    samples: deque[tuple[float, _Sample]] = field(default_factory=deque)
    state: ComplianceState = ComplianceState.NOT_VERIFIABLE
    violation_started_at: float | None = None
    alerted: bool = False
    alert_detail: str | None = None  # detalle de la última alerta emitida
    verified_span: float = 0.0
    verified_count: int = 0


@dataclass
class _PersonTrack:
    last_seen: float
    rules: dict[str, _RuleTrack] = field(default_factory=dict)


def _iou(a: Box, b: Box) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _center(box: Box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _association_cost(item: Box, person: Box, slot: str) -> float | None:
    """Coste de asignar un EPP a una persona, o None si no es compatible."""
    px1, py1, px2, py2 = person
    width, height = px2 - px1, py2 - py1
    if width <= 0 or height <= 0:
        return None
    x, y = _center(item)

    if slot in SLOT_HEAD:
        margin_x, y_min, y_max, anchor_y = 0.10 * width, py1 - 0.05 * height, py1 + 0.35 * height, py1 + 0.15 * height
    elif slot in SLOT_HANDS:
        # Los brazos extendidos pueden salir de la caja de la persona.
        margin_x, y_min, y_max, anchor_y = 0.30 * width, py1, py2 + 0.05 * height, py1 + 0.50 * height
    elif slot in SLOT_BODY:
        margin_x, y_min, y_max, anchor_y = 0.0, py1, py2, py1 + 0.45 * height
        item_area = max(0.0, item[2] - item[0]) * max(0.0, item[3] - item[1])
        overlap_w = max(0.0, min(item[2], px2) - max(item[0], px1))
        overlap_h = max(0.0, min(item[3], py2) - max(item[1], py1))
        if item_area <= 0 or (overlap_w * overlap_h) / item_area < 0.5:
            return None
    else:
        margin_x, y_min, y_max, anchor_y = 0.0, py1, py2, py1 + 0.50 * height

    if not (px1 - margin_x <= x <= px2 + margin_x and y_min <= y <= y_max):
        return None
    return (abs(x - (px1 + px2) / 2) + abs(y - anchor_y)) / height


def _point_box_distance(point: tuple[float, float], box: Box) -> float:
    """Distancia del punto al rectángulo (0 si está dentro)."""
    dx = max(box[0] - point[0], 0.0, point[0] - box[2])
    dy = max(box[1] - point[1], 0.0, point[1] - box[3])
    return (dx * dx + dy * dy) ** 0.5


def _visible_points(person: PersonDetection, indices: Sequence[int], min_conf: float) -> dict[int, tuple[float, float]]:
    keypoints = person.keypoints or ()
    return {
        index: (keypoints[index][0], keypoints[index][1])
        for index in indices
        if index < len(keypoints) and keypoints[index][2] >= min_conf
    }


def _torso_box(person: PersonDetection, min_conf: float, upper_torso_ratio: float = 0.0) -> Box | None:
    """Rectángulo hombros-caderas (al menos un hombro y una cadera visibles).

    Si las caderas no se ven (persona sentada o tras una mesa) y upper_torso_ratio > 0,
    se usa la parte alta del torso: desde los hombros hacia abajo, una altura igual a
    upper_torso_ratio veces el ancho de hombros. Requiere ver los dos hombros.
    """
    shoulders = _visible_points(person, SHOULDER_KEYPOINTS, min_conf)
    hips = _visible_points(person, HIP_KEYPOINTS, min_conf)
    if shoulders and not hips and upper_torso_ratio > 0 and len(shoulders) == 2:
        (ax, ay), (bx, by) = shoulders.values()
        width = abs(ax - bx)
        if width < 1.0:
            return None
        top = min(ay, by)
        return (min(ax, bx), top, max(ax, bx), top + upper_torso_ratio * width)
    if not shoulders or not hips:
        return None
    points = list(shoulders.values()) + list(hips.values())
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x1, x2 = min(xs), max(xs)
    if x2 - x1 < 1.0:  # persona de perfil: dar un ancho mínimo al torso
        half = 0.1 * (person.box[3] - person.box[1])
        x1, x2 = x1 - half, x2 + half
    return (x1, min(ys), x2, max(ys))


def torso_for_color(person: PersonDetection, config: 'ComplianceConfig | None' = None) -> Box | None:
    """Torso que el detector usa para medir el color de la ropa (el mismo que evalúa la bata)."""
    config = config or ComplianceConfig()
    return _torso_box(person, config.min_keypoint_confidence, config.coat_upper_torso_ratio)


def _coverage(inner: Box, outer: Box) -> float:
    """Proporción de `inner` cubierta por `outer`."""
    width = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    height = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    area = max(0.0, inner[2] - inner[0]) * max(0.0, inner[3] - inner[1])
    return width * height / area if area > 0 else 0.0


class ComplianceEngine:
    """Mantiene el estado de una cámara. Crear una instancia por cámara."""

    def __init__(self, rules: Iterable[EquipmentRule], config: ComplianceConfig | None = None):
        all_rules = list(rules)
        self.config = config or ComplianceConfig()
        self.rules = [rule for rule in all_rules if rule.model_class]
        # El modelo no puede comprobarlas: nunca generan una infracción.
        self.unsupported_rules = [rule for rule in all_rules if not rule.model_class]
        self._people: dict[int, _PersonTrack] = {}
        self._recent_events: dict[str, list[tuple[float, int, Box]]] = {}
        self._last_timestamp: float | None = None

    def update(self, frame: FrameObservation) -> ComplianceResult:
        if self._last_timestamp is not None and frame.timestamp < self._last_timestamp:
            raise ValueError('Los timestamps de una cámara no pueden retroceder')
        self._last_timestamp = frame.timestamp
        config = self.config

        persons = [p for p in frame.persons if p.confidence >= config.min_person_confidence]
        present = self._assign_equipment([p for p in persons if p.keypoints is None], frame.equipment)
        pose_samples = self._pose_samples([p for p in persons if p.keypoints is not None], frame.equipment)

        statuses: list[PersonEquipmentStatus] = []
        events: list[ViolationEvent] = []
        for person in persons:
            track = self._people.setdefault(person.track_id, _PersonTrack(last_seen=frame.timestamp))
            track.last_seen = frame.timestamp
            for rule in self.rules:
                rule_track = track.rules.setdefault(rule.equipment_id, _RuleTrack())
                if person.keypoints is not None:
                    sample = pose_samples[(person.track_id, rule.equipment_id)]
                elif not self._slot_visible(person, rule.slot, frame.frame_size):
                    sample = _Sample.UNKNOWN
                elif (person.track_id, rule.equipment_id) in present:
                    sample = _Sample.PRESENT
                else:
                    sample = _Sample.ABSENT
                ratio = self._advance(rule_track, frame.timestamp, sample)
                display, progress = self._display_state(rule_track, ratio)
                severity = detail = None
                if display is ComplianceState.VIOLATION:
                    detail = self._violation_detail(rule_track, rule)
                    severity = self.config.misplaced_severity if detail else rule.severity
                statuses.append(PersonEquipmentStatus(
                    person.track_id, rule.equipment_id, display, ratio, progress, severity, detail,
                ))
                event = self._maybe_emit(person, rule, rule_track, frame.timestamp, ratio)
                if event:
                    events.append(event)
            for rule in self.unsupported_rules:
                statuses.append(PersonEquipmentStatus(
                    person.track_id, rule.equipment_id, ComplianceState.NOT_VERIFIABLE, None,
                ))

        self._forget_stale(frame.timestamp)
        return ComplianceResult(frame.timestamp, tuple(statuses), tuple(events))

    def _assign_equipment(
        self, persons: Sequence[PersonDetection], equipment: Sequence[EquipmentDetection],
    ) -> set[tuple[int, str]]:
        present: set[tuple[int, str]] = set()
        for rule in self.rules:
            for item in equipment:
                if item.name != rule.model_class or item.confidence < self.config.min_equipment_confidence:
                    continue
                # Cada objeto se asigna solo a la persona más compatible, para no
                # dar por cumplido a alguien con el EPP de quien está a su lado.
                candidates = [
                    (cost, person.track_id)
                    for person in persons
                    if (cost := _association_cost(item.box, person.box, rule.slot)) is not None
                ]
                if candidates:
                    present.add((min(candidates)[1], rule.equipment_id))
        return present

    def _visible_parts(self, person: PersonDetection, slot: str) -> dict:
        """Partes del cuerpo visibles para una zona: {nombre: punto o caja}. Vacío = no verificable."""
        config = self.config
        min_conf = config.min_keypoint_confidence
        if person.box[3] - person.box[1] < config.min_person_height_px:
            return {}
        if person.visible_slots is not None and slot not in person.visible_slots:
            return {}
        if config.require_shoulder and not _visible_points(person, SHOULDER_KEYPOINTS, min_conf):
            # Solo manos o solo cara (primer plano): no se sabe de quién son ni si la persona está
            # en el encuadre; el modelo de pose llega a inventar una cara sobre unas manos.
            return {}
        if slot in SLOT_HANDS:
            return _visible_points(person, WRIST_KEYPOINTS, min_conf)
        if slot in SLOT_HEAD:
            face = _visible_points(person, FACE_KEYPOINTS, min_conf)
            if not face:
                return {}
            xs, ys = [p[0] for p in face.values()], [p[1] for p in face.values()]
            return {'cara': (sum(xs) / len(xs), sum(ys) / len(ys))}
        if slot in SLOT_BODY:
            torso = _torso_box(person, min_conf, config.coat_upper_torso_ratio)
            return {'torso': torso} if torso else {}
        return {}  # Zona sin soporte de pose (por ejemplo pies): no se evalúa.

    def _pose_samples(
        self, persons: Sequence[PersonDetection], equipment: Sequence[EquipmentDetection],
    ) -> dict[tuple[int, str], _Sample]:
        """Evalúa cada regla con puntos clave: zona visible primero, luego EPP cerca de ella.

        Guantes: cada muñeca visible se evalúa por separado; basta una muñeca
        visible sin guante para que la muestra sea AUSENTE.
        """
        config = self.config
        heights = {p.track_id: max(1.0, p.box[3] - p.box[1]) for p in persons}
        light = {p.track_id: p.torso_light for p in persons}
        samples: dict[tuple[int, str], _Sample] = {}
        for rule in self.rules:
            parts = {person.track_id: self._visible_parts(person, rule.slot) for person in persons}
            covered: dict[int, set] = {track_id: set() for track_id in parts}
            partial: dict[int, set] = {track_id: set() for track_id in parts}
            for item in equipment:
                if item.name != rule.model_class or item.confidence < config.min_equipment_confidence:
                    continue
                # Cada objeto va solo a la persona más compatible; dentro de ella
                # cubre todas las partes que alcance (por ejemplo, dos manos juntas).
                best: tuple[float, int, set] | None = None
                misplaced: tuple[float, int] | None = None  # lentes cerca, pero no en los ojos
                closed = True
                for track_id, person_parts in parts.items():
                    matched, best_cost = set(), None
                    for name, part in person_parts.items():
                        if rule.slot in SLOT_BODY:
                            coverage = _coverage(part, item.box)
                            ok, cost = coverage >= config.coat_open_min_coverage, 1.0 - coverage
                            if light[track_id] is not None and light[track_id] < config.coat_min_light:
                                ok = False  # torso oscuro: el modelo confundió la ropa con una bata
                        else:
                            limit = (config.glove_max_wrist_distance if rule.slot in SLOT_HANDS
                                     else config.goggles_max_face_distance)
                            cost = _point_box_distance(part, item.box) / heights[track_id]
                            ok = cost <= limit
                        if ok:
                            matched.add(name)
                            best_cost = cost if best_cost is None else min(best_cost, cost)
                    if matched and (best is None or best_cost < best[0]):
                        best = (best_cost, track_id, matched)
                    elif not matched and rule.slot in SLOT_HEAD and person_parts:
                        cost = min(_point_box_distance(part, item.box) for part in person_parts.values()) / heights[track_id]
                        if cost <= config.goggles_misplaced_max_distance and (misplaced is None or cost < misplaced[0]):
                            misplaced = (cost, track_id)
                if not best and misplaced:
                    partial[misplaced[1]] |= set(parts[misplaced[1]])
                if best:
                    if rule.slot in SLOT_BODY:
                        # Cerrada: cubre casi todo el torso y el torso es claro (con el color) o, sin
                        # dato de color, la confianza es alta. Si no, abierta.
                        covers = 1.0 - best[0] >= config.min_coat_torso_coverage
                        torso_light = light[best[1]]
                        closed = covers and (torso_light >= config.coat_closed_min_light if torso_light is not None
                                             else item.confidence >= config.coat_closed_min_confidence)
                    (covered if closed else partial)[best[1]] |= best[2]

            for track_id, person_parts in parts.items():
                if not person_parts:
                    sample = _Sample.UNKNOWN
                elif set(person_parts) <= covered[track_id]:
                    sample = _Sample.PRESENT
                elif set(person_parts) <= covered[track_id] | partial[track_id]:
                    sample = _Sample.PARTIAL
                elif (rule.slot in SLOT_BODY and light[track_id] is not None
                      and light[track_id] >= config.coat_min_light):
                    # Bata no detectada, pero el torso es claro: puede ser una bata que el modelo no vio
                    # (tapada por las manos, de lado). No es prueba de que falte. Sin bata, el torso
                    # medido nunca pasó de 0.04 en los videos del equipo.
                    sample = _Sample.UNKNOWN
                else:
                    sample = _Sample.ABSENT
                samples[(track_id, rule.equipment_id)] = sample
        return samples

    def _slot_visible(self, person: PersonDetection, slot: str, frame_size: tuple[int, int] | None) -> bool:
        x1, y1, x2, _ = person.box
        if person.box[3] - y1 < self.config.min_person_height_px:
            return False
        if person.visible_slots is not None and slot not in person.visible_slots:
            return False
        margin = self.config.edge_margin_px
        if slot in SLOT_HEAD and y1 <= margin:
            return False
        if frame_size and slot in SLOT_HANDS and (x1 <= margin or x2 >= frame_size[0] - margin):
            return False
        return True

    def _advance(self, track: _RuleTrack, now: float, sample: _Sample) -> float | None:
        config = self.config
        track.samples.append((now, sample))
        while track.samples and now - track.samples[0][0] > config.window_seconds:
            track.samples.popleft()

        verified = [item for item in track.samples if item[1] is not _Sample.UNKNOWN]
        if not verified:
            self._set_state(track, ComplianceState.NOT_VERIFIABLE, now)
            return None

        ratio = sum(item[1] is _Sample.PRESENT for item in verified) / len(verified)
        track.verified_span = verified[-1][0] - verified[0][0]
        track.verified_count = len(verified)
        enough = (
            len(verified) >= config.min_verified_samples
            and verified[-1][0] - verified[0][0] >= config.min_verified_seconds
        )
        if enough:
            if ratio <= config.violation_max_present_ratio:
                self._set_state(track, ComplianceState.VIOLATION, now)
            elif ratio >= config.compliant_min_present_ratio:
                self._set_state(track, ComplianceState.COMPLIANT, now)
            # Entre ambos umbrales se conserva el estado anterior (histéresis).
        return ratio

    def _display_state(self, track: _RuleTrack, ratio: float | None) -> tuple[ComplianceState, float]:
        """Estado para mostrar. No altera la decisión del motor (histéresis y alertas)."""
        config = self.config
        if track.state is ComplianceState.VIOLATION:
            return ComplianceState.VIOLATION, 1.0
        if ratio is None:
            return ComplianceState.NOT_VERIFIABLE, 0.0
        if ratio < config.compliant_min_present_ratio:
            # 100 % solo cuando se cumplen tiempo, muestras y proporción a la vez.
            by_time = min(1.0, track.verified_span / config.min_verified_seconds,
                          track.verified_count / config.min_verified_samples)
            span = config.compliant_min_present_ratio - config.violation_max_present_ratio
            by_ratio = min(1.0, max(0.0, (config.compliant_min_present_ratio - ratio) / span))
            return ComplianceState.AT_RISK, round(min(by_time, by_ratio), 2)
        if track.state is ComplianceState.COMPLIANT:
            return ComplianceState.COMPLIANT, 0.0
        return ComplianceState.NOT_VERIFIABLE, 0.0  # EPP presente, aún sin tiempo suficiente

    @staticmethod
    def _set_state(track: _RuleTrack, state: ComplianceState, now: float) -> None:
        if state is track.state:
            return
        track.state = state
        if state is ComplianceState.VIOLATION:
            track.violation_started_at = now
            track.alerted = False
        else:
            track.violation_started_at = None

    def _maybe_emit(
        self, person: PersonDetection, rule: EquipmentRule, track: _RuleTrack, now: float, ratio: float | None,
    ) -> ViolationEvent | None:
        if track.state is not ComplianceState.VIOLATION:
            return None
        detail = self._violation_detail(track, rule)
        # Una falta leve (bata abierta) que pasa a grave (se quita la bata) se avisa otra vez.
        escalated = track.alerted and track.alert_detail is not None and detail is None
        if track.alerted and not escalated:
            return None
        recent = [
            entry for entry in self._recent_events.get(rule.equipment_id, [])
            if now - entry[0] < self.config.cooldown_seconds
        ]
        self._recent_events[rule.equipment_id] = recent
        # Un cambio de track_id no debe duplicar la alerta de la misma persona.
        if not escalated and any(
            entry[1] == person.track_id or _iou(entry[2], person.box) >= self.config.same_person_iou
            for entry in recent
        ):
            return None
        track.alerted = True
        track.alert_detail = detail
        recent.append((now, person.track_id, person.box))
        return ViolationEvent(
            track_id=person.track_id,
            equipment_id=rule.equipment_id,
            model_class=rule.model_class or '',
            severity=self.config.misplaced_severity if detail else rule.severity,
            timestamp=now,
            started_at=track.violation_started_at or now,
            person_box=person.box,
            present_ratio=ratio if ratio is not None else 0.0,
            rule_code=rule.rule_code,
            detail=detail,
        )

    def _violation_detail(self, track: _RuleTrack, rule: EquipmentRule) -> str | None:
        """Detalle de "mal puesto" si casi todas las muestras sin el EPP correcto lo vieron mal puesto."""
        missing = [s for _, s in track.samples if s in (_Sample.ABSENT, _Sample.PARTIAL)]
        if not missing:
            return None
        share = sum(s is _Sample.PARTIAL for s in missing) / len(missing)
        if share < self.config.misplaced_min_ratio:
            return None
        return DETAIL_COAT_OPEN if rule.slot in SLOT_BODY else DETAIL_GOGGLES_OFF if rule.slot in SLOT_HEAD else None

    def _forget_stale(self, now: float) -> None:
        stale = [
            track_id for track_id, track in self._people.items()
            if now - track.last_seen > self.config.track_ttl_seconds
        ]
        for track_id in stale:
            del self._people[track_id]


def observation_from_record(record: Mapping) -> FrameObservation:
    """Crea una observación a partir de una línea JSON de tools/record_detections.py."""
    persons, equipment = [], []
    # Formato con pose: personas (con puntos clave) y EPP por separado.
    for detection in record.get('persons', []):
        if detection.get('track_id') is None:
            continue
        keypoints = detection.get('keypoints')
        persons.append(PersonDetection(
            int(detection['track_id']),
            tuple(float(value) for value in detection['box']),
            float(detection['conf']),
            keypoints=tuple(tuple(float(v) for v in kp) for kp in keypoints) if keypoints else None,
            torso_light=detection.get('torso_light'),
        ))
    for detection in record.get('equipment', []):
        if detection['name'] != 'person':
            equipment.append(EquipmentDetection(
                detection['name'], tuple(float(v) for v in detection['box']), float(detection['conf']),
            ))
    # Formato anterior: todas las detecciones de best.pt mezcladas.
    for detection in record.get('detections', []):
        box = tuple(float(value) for value in detection['box'])
        confidence = float(detection['conf'])
        if detection['name'] == 'person':
            if detection.get('track_id') is not None:
                persons.append(PersonDetection(int(detection['track_id']), box, confidence))
        else:
            equipment.append(EquipmentDetection(detection['name'], box, confidence))
    frame_size = record.get('frame_size')
    return FrameObservation(
        timestamp=float(record['timestamp']),
        persons=persons,
        equipment=equipment,
        frame_size=tuple(frame_size) if frame_size else None,
    )
