"""Pasa un video por los detectores una sola vez y guarda las detecciones en JSONL.

Uso: python tools/record_detections.py videos/caso.mp4 grabaciones/caso.jsonl

- Personas y seguimiento: --person-model (por defecto yolo11n-pose.pt, que además
  entrega 17 puntos clave). Con un modelo de detección como yolo11n.pt se usa la
  clase 0 (persona) sin puntos clave.
- EPP: --model (best.pt). Su clase `person` se ignora para no duplicar personas.

El video no se modifica ni se copia; solo se escriben coordenadas y confianzas.
"""
import argparse
import json
import time
from pathlib import Path

import sys

import cv2
from ultralytics import YOLO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.ai_vision.detectors import torso_light  # noqa: E402
from app.modules.reglas_lab.cumplimiento.motor import PersonDetection  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('video')
    parser.add_argument('output')
    parser.add_argument('--model', default='best.pt', help='Modelo de EPP')
    parser.add_argument('--person-model', default='yolo11n-pose.pt', help='Modelo de personas (pose o detección)')
    parser.add_argument('--analysis-fps', type=float, default=2.0)
    parser.add_argument('--source-fps', type=float, default=None, help='Solo si el video no informa sus FPS')
    parser.add_argument('--imgsz', type=int, default=640)
    parser.add_argument('--conf', type=float, default=0.25, help='Umbral del EPP')
    parser.add_argument('--person-conf', type=float, default=0.25, help='Umbral de personas')
    args = parser.parse_args()

    capture = cv2.VideoCapture(args.video)
    if not capture.isOpened():
        raise SystemExit(f'No se pudo abrir {args.video}')
    source_fps = args.source_fps or capture.get(cv2.CAP_PROP_FPS) or 25.0
    stride = max(1, round(source_fps / args.analysis_fps))

    equipment_model = YOLO(args.model)
    person_model = YOLO(args.person_model)
    is_pose = person_model.task == 'pose'

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    frames, index, started = 0, -1, time.perf_counter()
    person_seconds = equipment_seconds = 0.0
    with output.open('w', encoding='utf-8') as destination:
        while True:
            ok = capture.grab()
            if not ok:
                break
            index += 1
            if index % stride:
                continue
            ok, frame = capture.retrieve()
            if not ok:
                break

            t0 = time.perf_counter()
            people = person_model.track(
                frame, persist=True, conf=args.person_conf, imgsz=args.imgsz, classes=[0],
                tracker='bytetrack.yaml', verbose=False,
            )[0]
            t1 = time.perf_counter()
            items = equipment_model.predict(frame, conf=args.conf, imgsz=args.imgsz, verbose=False)[0]
            t2 = time.perf_counter()
            person_seconds += t1 - t0
            equipment_seconds += t2 - t1

            keypoints = people.keypoints.data.tolist() if is_pose and people.keypoints is not None else None
            persons = []
            for row, box in enumerate(people.boxes):
                persons.append({
                    'box': box.xyxy[0].tolist(),
                    'conf': float(box.conf[0]),
                    'track_id': int(box.id[0]) if box.id is not None else None,
                    'keypoints': [[round(v, 2) for v in point] for point in keypoints[row]] if keypoints else None,
                })
            # Color del torso (bata blanca u otra prenda), igual que en el monitoreo.
            for person in persons:
                if person['keypoints']:
                    detection = PersonDetection(0, tuple(person['box']), person['conf'],
                                                keypoints=tuple(tuple(point) for point in person['keypoints']))
                    person['torso_light'] = torso_light(frame, detection)
            equipment = []
            for box in items.boxes:
                name = equipment_model.names[int(box.cls[0])]
                if name == 'person':
                    continue
                equipment.append({'name': name, 'box': box.xyxy[0].tolist(), 'conf': float(box.conf[0])})

            height, width = frame.shape[:2]
            destination.write(json.dumps({
                'timestamp': round(index / source_fps, 3),
                'frame_size': [width, height],
                'persons': persons,
                'equipment': equipment,
            }) + '\n')
            frames += 1
    capture.release()
    elapsed = time.perf_counter() - started
    print(f'{frames} frames analizados en {elapsed:.0f} s -> {output}')
    if frames:
        print(f'Tiempo medio por frame: personas {1000 * person_seconds / frames:.0f} ms, '
              f'EPP {1000 * equipment_seconds / frames:.0f} ms (CPU de este equipo)')


if __name__ == '__main__':
    main()
