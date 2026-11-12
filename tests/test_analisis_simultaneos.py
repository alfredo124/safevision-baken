"""Cuántas cámaras analizan a la vez según el equipo (2026-11-12): regla por núcleos + medición."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import os
import threading
import time
import unittest
from unittest import mock

from app.modules.ai_vision import performance
from app.modules.ai_vision.detectors import EquipmentDetector
from app.modules.ai_vision.turnos import AnalysisTurns


class ParallelPipeline:
    """Escala con varios a la vez (sleep libera el GIL), como varios modelos en una CPU grande."""
    def analyze(self, frame, ts, conf):
        time.sleep(0.02)


_shared = threading.Lock()


class SerialPipeline:
    """No escala: todos comparten un recurso, como una CPU de 2 núcleos."""
    def analyze(self, frame, ts, conf):
        with _shared:
            time.sleep(0.02)


class ReglaTest(unittest.TestCase):
    def test_rule_by_physical_cores(self):
        self.assertEqual([performance.rule_for(c, False)['slots'] for c in (1, 2, 4, 8, 12, 16, 64)],
                         [1, 1, 1, 2, 3, 4, 8])
        self.assertEqual(performance.rule_for(12, False)['threads'], 4)
        self.assertEqual(performance.rule_for(2, False)['threads'], 2)
        self.assertEqual(performance.rule_for(8, True), {'slots': 2, 'threads': None})

    def test_candidates_and_choice(self):
        self.assertEqual(performance.candidates_for(3), [1, 2, 3])
        self.assertEqual(performance.candidates_for(8), [1, 2, 4, 8])
        self.assertEqual(performance.choose({1: 1.0, 2: 1.9, 3: 2.6}), 3)
        # Casi igual: se prefieren menos análisis a la vez.
        self.assertEqual(performance.choose({1: 1.0, 2: 1.05, 3: 1.02}), 1)


class PlanTest(unittest.TestCase):
    def setUp(self):
        performance.reset()
        self.addCleanup(performance.reset)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('SAFEVISION_ANALISIS_SIMULTANEOS', None)

    def plan(self, cores, factory, **kwargs):
        with mock.patch.object(performance, 'physical_cores', return_value=cores), \
                mock.patch.object(performance, '_set_torch_threads'):
            return performance.plan_analysis(factory, cuda=False, seconds=0.4, **kwargs)

    def test_small_pc_also_measures_one_and_two(self):
        """Un PC de 2 núcleos rindió un 45 % más con 2 análisis a la vez (medido el 2026-11-12)."""
        plan = self.plan(2, ParallelPipeline)
        self.assertEqual(set(plan['slots_measured']), {1, 2})
        self.assertEqual((plan['analysis_slots'], plan['slots_source']), (2, 'medido'))

    def test_failed_measurement_falls_back_to_rule(self):
        def broken():
            raise RuntimeError('sin modelo')
        plan = self.plan(2, broken)
        self.assertEqual((plan['analysis_slots'], plan['slots_source']), (1, 'regla'))

    def test_big_pc_measures_and_uses_parallel_when_it_scales(self):
        prepared = []
        plan = self.plan(12, ParallelPipeline, prepare=prepared.append)
        self.assertEqual(plan['slots_source'], 'medido')
        self.assertEqual(plan['analysis_slots'], 3)
        self.assertEqual(prepared, [1, 2, 3])  # copias del modelo de EPP antes de medir cada opción
        self.assertEqual(set(plan['slots_measured']), {1, 2, 3})

    def test_big_pc_keeps_one_when_parallel_does_not_help(self):
        plan = self.plan(12, SerialPipeline)
        self.assertEqual((plan['analysis_slots'], plan['slots_source']), (1, 'medido'))

    def test_manual_setting_wins(self):
        os.environ['SAFEVISION_ANALISIS_SIMULTANEOS'] = '2'
        plan = self.plan(32, mock.Mock(side_effect=AssertionError('no debe medir')))
        self.assertEqual((plan['analysis_slots'], plan['slots_source']), (2, 'manual'))

    def test_decided_once_per_process(self):
        first = self.plan(2, SerialPipeline)
        again = self.plan(12, mock.Mock(side_effect=AssertionError('ya estaba decidido')))
        self.assertEqual(again['analysis_slots'], first['analysis_slots'])


class EquipmentPoolTest(unittest.TestCase):
    def test_copies_let_two_analyses_run_together(self):
        active, peak, lock = [0], [0], threading.Lock()

        class FakeModel:
            names = {0: 'gloves'}

            def predict(self, *args, **kwargs):
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                time.sleep(0.05)
                with lock:
                    active[0] -= 1
                return [mock.Mock(boxes=[])]

        detector = EquipmentDetector(copies=1, loader=FakeModel)
        detector.ensure_copies(2)
        detector.ensure_copies(1)  # nunca quita copias
        self.assertEqual(detector.copies, 2)
        threads = [threading.Thread(target=detector.detect, args=(None, 0.25, 480)) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(peak[0], 2)

    def test_turns_follow_decided_slots(self):
        turns = AnalysisTurns()
        turns.set_slots(2)
        self.assertTrue(turns.acquire(1, False, timeout=1))
        self.assertTrue(turns.acquire(2, False, timeout=1), 'Con 2 análisis a la vez, la segunda no espera')
        self.assertFalse(turns.acquire(3, False, timeout=0.3))
        turns.release(1)
        self.assertTrue(turns.acquire(3, False, timeout=1))


if __name__ == '__main__':
    unittest.main()
