
import cv2
from ultralytics import YOLO
import time

def run_test():
    model = YOLO('yolo11n.pt')
    cap = cv2.VideoCapture(0)
    
    start_time = time.time()
    print('Iniciando prueba de 1 minuto...')
    
    while time.time() - start_time < 60:
        success, frame = cap.read()
        if not success:
            break
            
        # Inferencia
        results = model(frame)
        
        # Dibujar resultados
        annotated_frame = results[0].plot()
        
        cv2.imshow('YOLO11 Inferencia - SafeVision', annotated_frame)
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
            
    cap.release()
    cv2.destroyAllWindows()
    print('Prueba terminada.')

if __name__ == '__main__':
    run_test()
