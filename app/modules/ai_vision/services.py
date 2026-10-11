import cv2
from ultralytics import YOLO
import threading
import os

class VideoStreamProcessor:
    def __init__(self, model_path='yolo11n.pt'):
        self.model = YOLO(model_path)
        self.streams = {}
        self.lock = threading.Lock()
        self.latest_detections = {}

    def get_frame(self, camera_id, stream_url, draw_boxes=True):
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
        
        results = self.model(frame)
        detections = []
        for r in results:
            boxes = r.boxes
            for box in boxes:
                detections.append({
                    'box': box.xyxy[0].tolist(),
                    'conf': float(box.conf[0]),
                    'class': int(box.cls[0]),
                    'name': self.model.names[int(box.cls[0])]
                })
        
        self.latest_detections[camera_id] = detections
        
        if draw_boxes:
            frame = results[0].plot()
        
        ret, buffer = cv2.imencode('.jpg', frame)
        return buffer.tobytes(), detections

    def release_camera(self, camera_id):
        with self.lock:
            if camera_id in self.streams:
                self.streams[camera_id].release()
                del self.streams[camera_id]

video_processor = VideoStreamProcessor()
