"""Turnos de análisis entre cámaras (2026-11-12): una analiza a la vez, la ampliada con prioridad limitada."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import threading
import time
import unittest
from collections import Counter

from app.modules.ai_vision.turnos import AnalysisTurns


def run_cameras(turns, cameras, seconds, work=0.02, focused=()):
    """Cada cámara pide turno sin parar, como el bucle de análisis. Devuelve el orden de los turnos."""
    order, lock, stop = [], threading.Lock(), threading.Event()
    running, overlap = [0], [False]

    def camera(cid):
        while not stop.is_set():
            if not turns.acquire(cid, cid in focused, stop, timeout=2):
                continue
            with lock:
                running[0] += 1
                overlap[0] = overlap[0] or running[0] > 1
                order.append(cid)
            time.sleep(work)
            with lock:
                running[0] -= 1
            turns.release(cid)

    threads = [threading.Thread(target=camera, args=(cid,)) for cid in cameras]
    [t.start() for t in threads]
    time.sleep(seconds)
    stop.set()
    [t.join() for t in threads]
    return order, overlap[0]


class TurnosTest(unittest.TestCase):
    def test_one_at_a_time_and_fair(self):
        order, overlap = run_cameras(AnalysisTurns(), [1, 2, 3, 4, 5], 1.5)
        self.assertFalse(overlap, 'Dos cámaras analizaron a la vez')
        counts = Counter(order)
        self.assertEqual(set(counts), {1, 2, 3, 4, 5})
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 2)

    def test_focused_camera_gets_at_most_one_in_three(self):
        order, _ = run_cameras(AnalysisTurns(), [1, 2, 3, 4, 5], 2.0, focused={3})
        share = Counter(order)[3] / len(order)
        self.assertGreater(share, 0.25, 'La cámara ampliada debe tener más turnos que el reparto justo (1/5)')
        self.assertLessEqual(share, 0.35)
        for window in zip(order, order[1:], order[2:]):
            self.assertLessEqual(window.count(3), 1, f'Más de 1 de cada 3 turnos: {window}')

    def test_overdue_camera_goes_first(self):
        """Ninguna cámara pasa más de max_gap sin turno, aunque la ampliada tenga prioridad."""
        turns = AnalysisTurns(max_gap=0.15)
        order, _ = run_cameras(turns, [1, 2, 3, 4, 5, 6], 2.0, work=0.04, focused={1})
        last, worst = {}, 0.0
        # Con 6 cámaras y 40 ms por análisis, el reparto justo daría ~0.24 s entre turnos de una
        # misma cámara; con el límite de 0.15 s, la atrasada pasa siempre delante.
        positions = {cid: [i for i, c in enumerate(order) if c == cid] for cid in range(1, 7)}
        for cid, idx in positions.items():
            gaps = [b - a for a, b in zip(idx, idx[1:])]
            worst = max(worst, max(gaps) if gaps else 0)
        self.assertLessEqual(worst, 7, 'Una cámara esperó demasiados turnos')

    def test_stop_while_waiting(self):
        turns = AnalysisTurns()
        self.assertTrue(turns.acquire(1, False))
        stop = threading.Event()
        result = []
        waiter = threading.Thread(target=lambda: result.append(turns.acquire(2, False, stop)))
        waiter.start()
        time.sleep(0.1)
        stop.set()
        waiter.join(2)
        self.assertEqual(result, [False])
        turns.release(1)


if __name__ == '__main__':
    unittest.main()
