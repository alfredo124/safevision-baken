"""Evalúa las reglas sobre detecciones grabadas, sin cámara, GPU ni YOLO.

Uso: python tools/replay_compliance.py grabaciones/caso.jsonl --require gloves goggles lab_coat
     python tools/replay_compliance.py grabaciones/caso.jsonl --window 20 --min-verified 16
     python tools/replay_compliance.py grabaciones/caso.jsonl --scenes grabaciones/caso.scenes.json --min-scene 15

Con --scenes el motor se reinicia en cada corte de escena (videos editados) y
--min-scene descarta las escenas más cortas que ese número de segundos.

Muestra las infracciones que el motor emitiría y, por EPP, qué parte de las
muestras fue verificable (zona visible) y cuánto se clasificó como CUMPLE/INCUMPLE.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.reglas_lab.compliance import (
    ComplianceConfig, ComplianceEngine, ComplianceState, EquipmentRule, observation_from_record,
)

CLASSES = {
    'gloves': EquipmentRule('guantes', 'manos', 'gloves', 'ALTO'),
    'goggles': EquipmentRule('lentes', 'ojos', 'goggles', 'ALTO'),
    'lab_coat': EquipmentRule('chaleco', 'cuerpo', 'lab_coat', 'MEDIO'),
}


def replay(path, rules, config, scenes=None, min_scene=0.0):
    engine = ComplianceEngine(rules, config)
    current_scene = None
    states = defaultdict(Counter)
    verified = defaultdict(Counter)
    events, frames, track_ids = [], 0, set()
    with open(path, encoding='utf-8') as source:
        for line in source:
            observation = observation_from_record(json.loads(line))
            if scenes is not None:
                scene = next((sc for sc in scenes if sc['start'] <= observation.timestamp < sc['end']), None)
                if scene is None or scene['end'] - scene['start'] < min_scene:
                    continue
                if scene is not current_scene:
                    engine, current_scene = ComplianceEngine(rules, config), scene
            frames += 1
            track_ids.update(person.track_id for person in observation.persons)
            result = engine.update(observation)
            for status in result.statuses:
                states[status.equipment_id][status.state.value] += 1
                verified[status.equipment_id]['total'] += 1
                verified[status.equipment_id]['ok'] += status.present_ratio is not None
            events.extend(result.events)
    return frames, track_ids, states, verified, events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('recording')
    parser.add_argument('--require', nargs='+', choices=sorted(CLASSES), default=sorted(CLASSES))
    parser.add_argument('--window', type=float, help='Ventana en segundos')
    parser.add_argument('--min-verified', type=float, help='Segundos verificados exigidos')
    parser.add_argument('--keypoint-conf', type=float, help='Confianza mínima de un punto clave')
    parser.add_argument('--scenes', help='JSON de tools/detect_scenes.py')
    parser.add_argument('--min-scene', type=float, default=0.0, help='Descarta escenas más cortas (s)')
    args = parser.parse_args()

    config = ComplianceConfig()
    if args.window:
        config = replace(config, window_seconds=args.window,
                         min_verified_seconds=args.min_verified or 0.8 * args.window)
    elif args.min_verified:
        config = replace(config, min_verified_seconds=args.min_verified)
    if args.keypoint_conf is not None:
        config = replace(config, min_keypoint_confidence=args.keypoint_conf)

    frames, track_ids, states, verified, events = replay(
        args.recording, [CLASSES[name] for name in args.require], config,
        json.load(open(args.scenes, encoding='utf-8')) if args.scenes else None, args.min_scene,
    )
    for event in events:
        print(f't={event.timestamp:7.1f}s persona={event.track_id} falta={event.equipment_id} '
              f'severidad={event.severity} presencia={event.present_ratio:.0%}')
    print(f'Frames: {frames} | IDs de persona distintos: {len(track_ids)} | '
          f'ventana {config.window_seconds:g} s, verificado {config.min_verified_seconds:g} s')
    for equipment_id, counter in states.items():
        total = verified[equipment_id]['total']
        share = verified[equipment_id]['ok'] / total if total else 0
        detail = ', '.join(f'{state.value}={counter[state.value]}' for state in ComplianceState if counter[state.value])
        print(f'  {equipment_id:8s} verificable {share:5.0%} de {total} muestras persona | {detail}')
    print(f'Infracciones emitidas: {len(events)}')


if __name__ == '__main__':
    main()
