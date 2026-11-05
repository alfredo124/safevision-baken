"""Detecta cortes de escena en un video (para videos editados, no cámaras reales).

Uso: python tools/detect_scenes.py videos/caso.mp4 grabaciones/caso.scenes.json

Compara frames consecutivos a baja resolución: hay corte si la correlación del
histograma HSV cae bajo --threshold o la diferencia media de grises supera
--diff (0-255). No guarda imágenes.
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def detect(video: str, threshold: float = 0.6, diff: float = 35.0, min_scene_seconds: float = 1.0, sample_every: int = 2):
    capture = cv2.VideoCapture(video)
    if not capture.isOpened():
        raise SystemExit(f'No se pudo abrir {video}')
    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    cuts, previous, previous_gray, index = [0.0], None, None, -1
    while capture.grab():
        index += 1
        if index % sample_every:
            continue
        ok, frame = capture.retrieve()
        if not ok:
            break
        resized = cv2.resize(frame, (160, 90))
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY).astype(np.float32)
        small = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([small], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        if previous is not None:
            similarity = cv2.compareHist(previous, hist, cv2.HISTCMP_CORREL)
            now = index / fps
            change = float(np.abs(gray - previous_gray).mean())
            if (similarity < threshold or change > diff) and now - cuts[-1] >= min_scene_seconds:
                cuts.append(round(now, 2))
        previous, previous_gray = hist, gray
    capture.release()
    duration = round(total / fps, 2)
    return [{'scene': i, 'start': start, 'end': cuts[i + 1] if i + 1 < len(cuts) else duration}
            for i, start in enumerate(cuts)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('video')
    parser.add_argument('output')
    parser.add_argument('--threshold', type=float, default=0.6)
    parser.add_argument('--diff', type=float, default=35.0)
    args = parser.parse_args()
    scenes = detect(args.video, args.threshold, args.diff)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(scenes, indent=1), encoding='utf-8')
    lengths = [scene['end'] - scene['start'] for scene in scenes]
    print(f'{len(scenes)} escenas -> {args.output} | >=15 s: {sum(x >= 15 for x in lengths)} '
          f'| >=30 s: {sum(x >= 30 for x in lengths)} | media {sum(lengths) / len(lengths):.1f} s')


if __name__ == '__main__':
    main()
