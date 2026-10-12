import cv2
from ultralytics import YOLO
import threading
import os
import time
from pathlib import Path

class VideoStreamProcessor:
    def __init__(self, model_path='best.pt'):
        project_root = Path(__file__).resolve().parents[3]
        model_file = Path(model_path)
        if not model_file.is_absolute():
            model_file = project_root / model_file
        self.model = YOLO(str(model_file))
        self.streams = {}
        self.lock = threading.Lock()
        self.latest_detections = {}
        self.last_inference_at = {}

    def get_frame(self, camera_id, stream_url, draw_boxes=True, confidence_threshold=0.5, processing_fps=10):
        with self.lock:
            if camera_id not in self.streams:
                source = stream_url if (os.path.exists(stream_url) or not stream_url.isdigit()) else int(stream_url)
                print(f"DEBUG: Intentando abrir stream: {source}")
                cap = cv2.VideoCapture(source)
                if not cap.isOpened():
                    print(f"DEBUG: ERROR al abrir {source}")
                self.streams[camera_id] = cap
        
        cap = self.streams[camera_id]
        success, frame = cap.read()
        
        if not success:
            if not stream_url.isdigit():
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                success, frame = cap.read()
            if not success:
                return None, []
        
        now = time.monotonic()
        interval = 1 / max(1, processing_fps)
        with self.lock:
            should_infer = now - self.last_inference_at.get(camera_id, 0) >= interval
            if should_infer:
                self.last_inference_at[camera_id] = now

        detections = self.latest_detections.get(camera_id, [])
        if should_infer:
            results = self.model.predict(frame, conf=confidence_threshold, verbose=False)
            detections = []
            for result in results:
                for box in result.boxes:
                    class_id = int(box.cls[0])
                    detections.append({
                        'box': box.xyxy[0].tolist(),
                        'conf': float(box.conf[0]),
                        'class': class_id,
                        'name': self.model.names[class_id]
                    })
            self.latest_detections[camera_id] = detections

        if draw_boxes:
            for detection in detections:
                x1, y1, x2, y2 = map(int, detection['box'])
                label = f"{detection['name']} {detection['conf']:.2f}"
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 0), 2)
                cv2.putText(frame, label, (x1, max(y1 - 8, 0)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, (0, 200, 0), 2)
        
        ret, buffer = cv2.imencode('.jpg', frame)
        return buffer.tobytes(), detections

    def release_camera(self, camera_id):
        with self.lock:
            if camera_id in self.streams:
                self.streams[camera_id].release()
                del self.streams[camera_id]
                self.last_inference_at.pop(camera_id, None)
                self.latest_detections.pop(camera_id, None)

video_processor = VideoStreamProcessor()
