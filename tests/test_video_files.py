"""Los videos subidos se guardan a 15 fps y 640 px (con 60 fps la CPU no alcanzaba para analizar)."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.core import database
from app.core.security import create_access_token
from app.main import app
from app.modules.ai_vision import router_cameras
from app.modules.ai_vision.video_files import needs_optimization, optimize_video, video_info


def video_de_prueba(path: Path, fps=60, size=(1280, 720), seconds=2):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), fps, size)
    for index in range(int(fps * seconds)):
        frame = np.full((size[1], size[0], 3), (index * 3) % 255, np.uint8)
        writer.write(frame)
    writer.release()


class VideoFilesTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='safevision-tests-videofiles-'))  # nunca videos/ real

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_optimiza_a_15_fps_y_640_px(self):
        source, target = self.dir / 'alto.mp4', self.dir / 'bajo.mp4'
        video_de_prueba(source)
        self.assertTrue(needs_optimization(source))
        self.assertTrue(optimize_video(source, target))
        fps, width, height = video_info(target)
        self.assertLessEqual(fps, 15.5)
        self.assertEqual((width, height), (640, 360))
        self.assertFalse(needs_optimization(target))

    def test_video_pequeno_no_se_toca(self):
        source = self.dir / 'chico.mp4'
        video_de_prueba(source, fps=12, size=(320, 240))
        self.assertFalse(needs_optimization(source))

    def test_archivo_no_valido(self):
        source = self.dir / 'roto.mp4'
        source.write_bytes(b'no es un video')
        self.assertFalse(needs_optimization(source))
        self.assertFalse(optimize_video(source, self.dir / 'salida.mp4'))

    def test_subida_queda_optimizada(self):
        assert 'safevision-tests-' in database.SQLALCHEMY_DATABASE_URL
        upload_dir = self.dir / 'subidos'
        upload_dir.mkdir()
        source = self.dir / 'celular.mp4'
        video_de_prueba(source)
        admin = {'Authorization': f"Bearer {create_access_token({'sub': 'admin_video', 'role': 'ADMINISTRADOR'})}"}
        with TestClient(app) as client, mock.patch.object(router_cameras, 'VIDEO_UPLOAD_DIR', upload_dir):
            from tools.create_admin import create_admin
            create_admin('admin_video', 'admin-pass', 'Admin Video')
            with source.open('rb') as file:
                response = client.post('/modules/ai_vision/cameras/upload', headers=admin,
                                       data={'name': 'Celular', 'laboratory_type': 'QUIMICA'},
                                       files={'video': ('celular.mp4', file, 'video/mp4')})
        self.assertEqual(response.status_code, 201, response.text)
        stored = Path(response.json()['stream_url'])
        self.assertTrue(stored.name.endswith('-15fps.mp4'))
        self.assertEqual([p.name for p in upload_dir.iterdir()], [stored.name], 'No queda el original duplicado')
        self.assertLessEqual(video_info(stored)[0], 15.5)


if __name__ == '__main__':
    unittest.main()
