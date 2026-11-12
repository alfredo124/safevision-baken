"""Compara el motor de cumplimiento con tramos etiquetados a mano (Tarea 1).

Uso: python tools/evaluate_labels.py grabaciones/caso_pose.jsonl grabaciones/caso.labels.json
     python tools/evaluate_labels.py ... --windows 5 10 15 20 30 --equip-conf goggles=0.1
     python tools/evaluate_labels.py ... --coat-coverage 0.7

Etiquetas (JSON): {"clases": {"goggles": [[inicio, fin, "si"|"no"|"abierta"|"?"], ...], ...},
                   "escenas": "grabaciones/caso.scenes.json" (opcional, reinicia el motor en cada corte)}

Muestra dos tablas por clase:
- Muestras: lo que el motor ve en cada frame (PRESENTE / AUSENTE / sin verificar)
  frente a la etiqueta. Mide la calidad de la evidencia, antes del periodo.
- Eventos, por periodo: infracciones esperadas (tramos "no" o "abierta" de al menos el
  tiempo verificado exigido), detectadas, con la gravedad correcta (abierta = leve,
  no = grave), omitidas, falsas alarmas y segundos hasta alertar.
No usa imágenes; solo coordenadas grabadas por tools/record_detections.py.
"""
import argparse
import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.reglas_lab.compliance import (
    DETAIL_COAT_OPEN, ComplianceConfig, ComplianceEngine, observation_from_record,
)
from tools.replay_compliance import CLASSES

GRACE_SECONDS = 2.0  # margen tras el fin de un tramo "no" para aceptar la alerta


def label_at(segments, t):
    for start, end, value in segments:
        if start <= t < end:
            return value
    return '?'


def expected_violations(segments, scenes, min_seconds):
    """Tramos "no" o "abierta" dentro de una misma escena, de al menos min_seconds."""
    result = []
    for start, end, value in segments:
        if value not in ('no', 'abierta'):
            continue
        cuts = [start] + [s['start'] for s in scenes if start < s['start'] < end] + [end]
        for a, b in zip(cuts, cuts[1:]):
            if b - a >= min_seconds:
                result.append((a, b, DETAIL_COAT_OPEN if value == 'abierta' else None))
    return result


def run(records, labels, scenes, config, equipment_conf):
    """Pasa las grabaciones por el motor. Devuelve muestras por clase y eventos."""
    rules = [CLASSES[name] for name in labels]
    engine, scene = None, None
    samples = {name: Counter() for name in labels}
    events = []
    for record in records:
        observation = observation_from_record(record)
        # Umbral de confianza por clase: se filtra antes del motor.
        observation = replace(observation, equipment=[
            item for item in observation.equipment
            if item.confidence >= equipment_conf.get(item.name, config.min_equipment_confidence)
        ])
        current = next((s for s in scenes if s['start'] <= observation.timestamp < s['end']), None) if scenes else None
        if engine is None or current is not scene:
            engine, scene = ComplianceEngine(rules, config), current
        pose_persons = [p for p in observation.persons
                        if p.keypoints is not None and p.confidence >= config.min_person_confidence]
        per_frame = engine._pose_samples(pose_persons, observation.equipment)
        for name, rule in ((n, CLASSES[n]) for n in labels):
            truth = label_at(labels[name], observation.timestamp)
            for person in pose_persons:
                samples[name][(truth, per_frame[(person.track_id, rule.equipment_id)].value)] += 1
        events.extend(engine.update(observation).events)
    return samples, events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('recording')
    parser.add_argument('labels')
    parser.add_argument('--windows', type=float, nargs='+', default=[5, 10, 15, 20, 30])
    parser.add_argument('--verified-ratio', type=float, default=0.8, help='Segundos verificados = ventana x este valor')
    parser.add_argument('--equip-conf', nargs='*', default=[], help='Umbral por clase, p. ej. goggles=0.1')
    parser.add_argument('--coat-coverage', type=float, help='min_coat_torso_coverage')
    parser.add_argument('--keypoint-conf', type=float, help='min_keypoint_confidence')
    parser.add_argument('--coat-closed-conf', type=float, help='coat_closed_min_confidence')
    parser.add_argument('--coat-open-ratio', type=float, help='misplaced_min_ratio')
    args = parser.parse_args()

    label_file = json.load(open(args.labels, encoding='utf-8'))
    labels = label_file['clases']
    scenes = json.load(open(label_file['escenas'], encoding='utf-8')) if label_file.get('escenas') else []
    records = [json.loads(line) for line in open(args.recording, encoding='utf-8')]
    equipment_conf = {key: float(value) for key, value in (item.split('=') for item in args.equip_conf)}

    base = ComplianceConfig()
    if args.coat_coverage is not None:
        base = replace(base, min_coat_torso_coverage=args.coat_coverage)
    if args.keypoint_conf is not None:
        base = replace(base, min_keypoint_confidence=args.keypoint_conf)
    if args.coat_closed_conf is not None:
        base = replace(base, coat_closed_min_confidence=args.coat_closed_conf)
    if args.coat_open_ratio is not None:
        base = replace(base, misplaced_min_ratio=args.coat_open_ratio)

    print(f'{Path(args.recording).name} | {len(records)} frames | escenas: {len(scenes) or 1} | '
          f'umbral EPP {base.min_equipment_confidence} {equipment_conf or ""} | '
          f'bata cerrada: conf {base.coat_closed_min_confidence}, cobertura {base.min_coat_torso_coverage}')

    samples, _ = run(records, labels, scenes, base, equipment_conf)
    print('\nMuestras por frame (etiqueta -> lo que ve el motor)')
    print(f'{"clase":9s} {"etiqueta":8s} {"PRESENTE":>9s} {"MAL PUESTO":>10s} {"AUSENTE":>8s} {"sin ver":>8s}')
    for name, counter in samples.items():
        for truth in ('si', 'abierta', 'no'):
            row = [counter[(truth, s)] for s in ('present', 'partial', 'absent', 'unknown')]
            if sum(row):
                print(f'{name:9s} {truth:8s} {row[0]:9d} {row[1]:10d} {row[2]:8d} {row[3]:8d}')

    print('\nEventos por periodo (ventana / verificado)')
    print(f'{"clase":9s} {"periodo":>9s} {"esperadas":>9s} {"detect.":>7s} {"grav.ok":>7s} {"omitidas":>8s} '
          f'{"falsas":>6s} {"s hasta alertar":>16s}')
    for window in args.windows:
        config = replace(base, window_seconds=window, min_verified_seconds=args.verified_ratio * window)
        _, events = run(records, labels, scenes, config, equipment_conf)
        for name in labels:
            rule = CLASSES[name]
            expected = expected_violations(labels[name], scenes, config.min_verified_seconds)
            mine = [e for e in events if e.equipment_id == rule.equipment_id]
            hits, delays, right = set(), [], set()
            false_alarms = 0
            for event in mine:
                match = next((i for i, (a, b, _) in enumerate(expected) if a <= event.timestamp <= b + GRACE_SECONDS), None)
                if match is not None:
                    if match not in hits:
                        delays.append(event.timestamp - expected[match][0])
                    hits.add(match)
                    if event.detail == expected[match][2]:
                        right.add(match)
                elif label_at(labels[name], event.timestamp) == 'si':
                    false_alarms += 1
            delay = ', '.join(f'{d:.0f}' for d in delays) or '-'
            print(f'{name:9s} {window:4g}/{config.min_verified_seconds:<4g} {len(expected):9d} {len(hits):7d} '
                  f'{len(right):7d} {len(expected) - len(hits):8d} {false_alarms:6d} {delay:>16s}')


if __name__ == '__main__':
    main()
