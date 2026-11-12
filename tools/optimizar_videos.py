"""Convierte los videos de las cámaras de archivo a 15 fps y 640 px (ver app/modules/ai_vision/video_files.py).

Uso (con el backend DETENIDO: en Windows un video abierto no se puede mover):
    python tools/optimizar_videos.py               # solo muestra qué haría
    python tools/optimizar_videos.py --confirmar   # convierte

Cada original se mueve a videos/cameras/originales/ (no se borra) y la versión optimizada queda con
el mismo nombre, así la base de datos no cambia. Solo lee la base de datos.
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.modules.ai_vision.models import Camera  # noqa: E402
from app.modules.ai_vision.router_cameras import VIDEO_UPLOAD_DIR  # noqa: E402
from app.modules.ai_vision.video_files import needs_optimization, optimize_video, video_info  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--confirmar', action='store_true', help='Convertir de verdad (sin esto solo se lista)')
    args = parser.parse_args()

    with SessionLocal() as db:
        cameras = db.query(Camera).filter(Camera.is_active.is_(True)).all()
        files = [(camera.id, camera.name, Path(camera.stream_url)) for camera in cameras]
    backup_dir = VIDEO_UPLOAD_DIR / 'originales'
    pending = []
    for camera_id, name, path in files:
        if not path.is_file() or VIDEO_UPLOAD_DIR.resolve() not in path.resolve().parents:
            continue  # no es un video subido (webcam, RTSP, navegador)
        info = video_info(path)
        state = 'convertir' if needs_optimization(path) else 'ya optimizado'
        fps, width, height = info if info else (0, 0, 0)
        print(f'Cámara {camera_id} ({name}): {path.name} · {fps:.0f} fps {width}x{height} -> {state}')
        if state == 'convertir':
            pending.append(path)

    if not pending:
        print('Nada que convertir.')
        return
    if not args.confirmar:
        print(f'\n{len(pending)} video(s) por convertir. Detén el backend y repite con --confirmar.')
        return

    backup_dir.mkdir(exist_ok=True)
    for path in pending:
        backup = backup_dir / path.name
        try:
            shutil.move(str(path), str(backup))
        except PermissionError:
            print(f'{path.name}: está abierto (¿el backend sigue corriendo?). No se tocó.')
            continue
        if optimize_video(backup, path):
            print(f'{path.name}: optimizado. Original en {backup}')
        else:
            shutil.move(str(backup), str(path))  # se deja como estaba
            print(f'{path.name}: no se pudo convertir; se dejó el original.')


if __name__ == '__main__':
    main()
