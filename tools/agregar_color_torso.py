"""Añade a una grabación de detecciones el color del torso de cada persona (torso_light), leyendo el video.

Uso: python tools/agregar_color_torso.py grabaciones/caso_pose.jsonl videos/caso.mp4
     python tools/agregar_color_torso.py grabaciones/caso_pose.jsonl videos/caso.mp4 --output otra.jsonl

Sirve para grabaciones hechas antes de que tools/record_detections.py guardara este dato, sin volver a
pasar YOLO. Sin --output, reescribe la grabación (antes guarda una copia .bak).
El motor usa torso_light para distinguir una bata blanca de otra prenda (ComplianceConfig.coat_*_light).
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.ai_vision.detectors import torso_light  # noqa: E402
from app.modules.reglas_lab.compliance import PersonDetection  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('recording')
    parser.add_argument('video')
    parser.add_argument('--output')
    args = parser.parse_args()

    source = Path(args.recording)
    rows = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines() if line.strip()]
    capture = cv2.VideoCapture(args.video)
    if not capture.isOpened():
        raise SystemExit(f'No se pudo abrir {args.video}')
    measured = 0
    for row in rows:
        capture.set(cv2.CAP_PROP_POS_MSEC, row['timestamp'] * 1000)
        ok, frame = capture.read()
        if not ok:
            continue
        width, height = row.get('frame_size') or (frame.shape[1], frame.shape[0])
        if (frame.shape[1], frame.shape[0]) != (width, height):
            frame = cv2.resize(frame, (width, height))  # misma escala que las coordenadas grabadas
        for person in row.get('persons', []):
            if not person.get('keypoints'):
                continue
            detection = PersonDetection(0, tuple(person['box']), person['conf'],
                                        keypoints=tuple(tuple(point) for point in person['keypoints']))
            person['torso_light'] = torso_light(frame, detection)
            measured += person['torso_light'] is not None
    capture.release()

    output = Path(args.output) if args.output else source
    if not args.output:
        shutil.copyfile(source, source.with_suffix(source.suffix + '.bak'))
    output.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    print(f'{measured} personas con color de torso -> {output}')


if __name__ == '__main__':
    main()
