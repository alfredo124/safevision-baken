"""Umbral de confianza e imgsz (Tarea 0, paso 4)."""
import unittest
from pathlib import Path

from app.modules.ai_vision.services import VideoStreamProcessor

ROOT = Path(__file__).resolve().parents[1]


def _processor_without_model(image_size=640, floor=0.25):
    processor = VideoStreamProcessor.__new__(VideoStreamProcessor)
    processor.image_size = image_size
    processor.confidence_floor = floor
    return processor


class PredictOptionsTest(unittest.TestCase):
    def test_threshold_above_floor_is_respected(self):
        self.assertEqual(_processor_without_model().predict_options(0.5)['conf'], 0.5)
        self.assertEqual(_processor_without_model().predict_options(0.8)['conf'], 0.8)

    def test_threshold_below_floor_is_respected(self):
        self.assertEqual(_processor_without_model().predict_options(0.1)['conf'], 0.1)

    def test_invalid_threshold_uses_floor(self):
        for value in (None, 0, -1, 1.5):
            self.assertEqual(_processor_without_model().predict_options(value)['conf'], 0.25)

    def test_default_image_size_matches_training(self):
        import inspect
        default = inspect.signature(VideoStreamProcessor.__init__).parameters['image_size'].default
        self.assertEqual(default, 640)
        self.assertEqual(_processor_without_model().predict_options(0.5)['imgsz'], 640)


@unittest.skipUnless((ROOT / 'best.pt').exists() and (ROOT / 'videos' / 'videoplayback.mp4').exists(),
                     'Requiere best.pt y videos/videoplayback.mp4')
class RealModelThresholdTest(unittest.TestCase):
    """Con YOLO real: subir el umbral reduce las detecciones."""

    def test_raising_threshold_changes_detections(self):
        import cv2
        from ultralytics import YOLO

        model = YOLO(str(ROOT / 'best.pt'))
        processor = _processor_without_model()
        capture = cv2.VideoCapture(str(ROOT / 'videos' / 'videoplayback.mp4'))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 100
        low = high = 0
        for position in range(0, total, max(1, total // 6))[:6]:
            capture.set(cv2.CAP_PROP_POS_FRAMES, position)
            ok, frame = capture.read()
            if not ok:
                continue
            low += len(model.predict(frame, **processor.predict_options(0.25))[0].boxes)
            high += len(model.predict(frame, **processor.predict_options(0.6))[0].boxes)
        capture.release()
        print(f'\n  detecciones en 6 frames: conf=0.25 -> {low}, conf=0.6 -> {high}')
        self.assertGreater(low, 0, 'El modelo no detectó nada; revisar el video de prueba')
        self.assertLess(high, low)


if __name__ == '__main__':
    unittest.main()
