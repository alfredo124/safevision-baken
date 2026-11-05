import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.modules.reglas_lab.compliance import (
    ComplianceConfig, ComplianceEngine, ComplianceState, EquipmentDetection, EquipmentRule,
    FrameObservation, PersonDetection, observation_from_record,
)

ROOT = Path(__file__).resolve().parents[1]
GLOVES = EquipmentRule('guantes', 'manos', 'gloves', 'ALTO')
GOGGLES = EquipmentRule('lentes', 'ojos', 'goggles', 'ALTO')
COAT = EquipmentRule('chaleco', 'cuerpo', 'lab_coat', 'MEDIO')
BOOTS = EquipmentRule('botas', 'pies', None, 'MEDIO')
PERSON_BOX = (100.0, 100.0, 200.0, 400.0)
OTHER_BOX = (400.0, 100.0, 500.0, 400.0)
FPS = 2


def glove(box=PERSON_BOX, confidence=0.8):
    x = box[0] + 10
    return EquipmentDetection('gloves', (x, box[1] + 150, x + 20, box[1] + 170), confidence)


def goggles(box=PERSON_BOX):
    return EquipmentDetection('goggles', (box[0] + 30, box[1] + 20, box[0] + 70, box[1] + 40), 0.8)


def coat(box=PERSON_BOX):
    return EquipmentDetection('lab_coat', (box[0], box[1] + 50, box[2], box[3] - 50), 0.8)


def run(engine, seconds, persons, equipment=lambda second: [], start=0.0, frame_size=(640, 480)):
    events, result = [], None
    for index in range(int(seconds * FPS)):
        now = start + index / FPS
        result = engine.update(FrameObservation(now, persons(now), equipment(now), frame_size))
        events.extend(result.events)
    return events, result


def person(track_id=1, box=PERSON_BOX, **kwargs):
    return lambda now: [PersonDetection(track_id, box, 0.9, **kwargs)]


def state_of(result, track_id, equipment_id):
    return next(s.state for s in result.statuses if s.track_id == track_id and s.equipment_id == equipment_id)


class TemporalRules(unittest.TestCase):
    def test_person_with_all_equipment_generates_no_event(self):
        engine = ComplianceEngine([GLOVES, GOGGLES, COAT])
        events, result = run(engine, 20, person(), lambda now: [glove(), goggles(), coat()])
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'lentes'), ComplianceState.COMPLIANT)

    def test_missing_equipment_alerts_once_after_minimum_time(self):
        events, result = run(ComplianceEngine([GLOVES]), 30, person())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].equipment_id, 'guantes')
        self.assertGreaterEqual(events[0].timestamp, ComplianceConfig().min_verified_seconds)
        self.assertEqual(state_of(result, 1, 'guantes'), ComplianceState.VIOLATION)

    def test_brief_absence_does_not_alert(self):
        equipment = lambda now: [] if 5 <= now < 7 else [glove()]
        events, _ = run(ComplianceEngine([GLOVES]), 20, person(), equipment)
        self.assertEqual(events, [])

    def test_intermittent_detection_does_not_alert(self):
        generator = random.Random(7)
        events, _ = run(ComplianceEngine([GLOVES]), 300, person(),
                        lambda now: [glove()] if generator.random() < 0.63 else [])
        self.assertEqual(events, [])

    def test_violation_resolves_when_equipment_appears(self):
        equipment = lambda now: [glove()] if now >= 20 else []
        events, result = run(ComplianceEngine([GLOVES]), 40, person(), equipment)
        self.assertEqual(len(events), 1)
        self.assertEqual(state_of(result, 1, 'guantes'), ComplianceState.COMPLIANT)

    def test_low_confidence_equipment_is_ignored(self):
        events, _ = run(ComplianceEngine([GLOVES]), 30, person(), lambda now: [glove(confidence=0.1)])
        self.assertEqual(len(events), 1)

    def test_timestamps_cannot_go_backwards(self):
        engine = ComplianceEngine([GLOVES])
        engine.update(FrameObservation(5.0, [], []))
        with self.assertRaises(ValueError):
            engine.update(FrameObservation(4.0, [], []))

    def test_invalid_ratios_are_rejected(self):
        with self.assertRaises(ValueError):
            ComplianceConfig(violation_max_present_ratio=0.6, compliant_min_present_ratio=0.5)


class Association(unittest.TestCase):
    def test_only_the_person_without_equipment_alerts(self):
        persons = lambda now: [PersonDetection(1, PERSON_BOX), PersonDetection(2, OTHER_BOX)]
        events, _ = run(ComplianceEngine([GLOVES]), 30, persons, lambda now: [glove(PERSON_BOX)])
        self.assertEqual([event.track_id for event in events], [2])

    def test_one_glove_between_two_people_counts_for_the_nearest_only(self):
        left, right = (100.0, 100.0, 200.0, 400.0), (210.0, 100.0, 310.0, 400.0)
        item = EquipmentDetection('gloves', (185, 240, 195, 260), 0.8)  # dentro del margen de ambos
        persons = lambda now: [PersonDetection(1, left), PersonDetection(2, right)]
        events, _ = run(ComplianceEngine([GLOVES]), 30, persons, lambda now: [item])
        self.assertEqual([event.track_id for event in events], [2])

    def test_goggles_at_waist_height_do_not_count(self):
        item = EquipmentDetection('goggles', (130, 280, 170, 300), 0.8)
        events, _ = run(ComplianceEngine([GOGGLES]), 30, person(), lambda now: [item])
        self.assertEqual(len(events), 1)

    def test_coat_of_a_distant_person_does_not_count(self):
        events, _ = run(ComplianceEngine([COAT]), 30, person(), lambda now: [coat(OTHER_BOX)])
        self.assertEqual(len(events), 1)


class Visibility(unittest.TestCase):
    def test_small_person_is_not_verifiable(self):
        events, result = run(ComplianceEngine([GLOVES]), 20, person(box=(100, 100, 120, 150)))
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'guantes'), ComplianceState.NOT_VERIFIABLE)

    def test_head_cut_by_frame_does_not_alert_goggles(self):
        events, result = run(ComplianceEngine([GOGGLES]), 20, person(box=(100, 0, 200, 300)))
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'lentes'), ComplianceState.NOT_VERIFIABLE)

    def test_external_visibility_hides_hands(self):
        events, result = run(ComplianceEngine([GLOVES]), 20,
                             person(visible_slots=frozenset({'ojos', 'cuerpo'})))
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'guantes'), ComplianceState.NOT_VERIFIABLE)

    def test_rule_without_model_class_never_alerts(self):
        engine = ComplianceEngine([BOOTS])
        events, result = run(engine, 20, person())
        self.assertEqual(events, [])
        self.assertEqual([rule.equipment_id for rule in engine.unsupported_rules], ['botas'])
        self.assertEqual(state_of(result, 1, 'botas'), ComplianceState.NOT_VERIFIABLE)


class Deduplication(unittest.TestCase):
    def test_track_id_switch_does_not_duplicate_alert(self):
        persons = lambda now: [PersonDetection(1 if now < 20 else 2, PERSON_BOX)]
        events, _ = run(ComplianceEngine([GLOVES]), 60, persons)
        self.assertEqual(len(events), 1)

    def test_same_person_can_alert_again_after_cooldown(self):
        equipment = lambda now: [glove()] if 30 <= now < 100 else []
        events, _ = run(ComplianceEngine([GLOVES]), 140, person(), equipment)
        self.assertEqual(len(events), 2)

    def test_different_person_elsewhere_alerts_during_cooldown(self):
        persons = lambda now: [PersonDetection(1, PERSON_BOX)] if now < 20 else [PersonDetection(2, OTHER_BOX)]
        events, _ = run(ComplianceEngine([GLOVES]), 60, persons)
        self.assertEqual(sorted(event.track_id for event in events), [1, 2])

    def test_stale_tracks_are_forgotten(self):
        engine = ComplianceEngine([GLOVES])
        run(engine, 5, person())
        engine.update(FrameObservation(100.0, [], []))
        self.assertEqual(engine._people, {})


class Replay(unittest.TestCase):
    def test_record_conversion_skips_untracked_people(self):
        observation = observation_from_record({'timestamp': 1, 'frame_size': [640, 480], 'detections': [
            {'name': 'person', 'box': [1, 2, 3, 4], 'conf': .9, 'track_id': 5},
            {'name': 'person', 'box': [1, 2, 3, 4], 'conf': .9, 'track_id': None},
            {'name': 'gloves', 'box': [1, 2, 3, 4], 'conf': .9, 'track_id': 9},
        ]})
        self.assertEqual([p.track_id for p in observation.persons], [5])
        self.assertEqual([e.name for e in observation.equipment], ['gloves'])

    def test_replay_tool_reports_the_violation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'caso.jsonl'
            path.write_text('\n'.join(json.dumps({
                'timestamp': index / 2, 'frame_size': [640, 480],
                'detections': [{'name': 'person', 'box': list(PERSON_BOX), 'conf': .9, 'track_id': 1}],
            }) for index in range(60)), encoding='utf-8')
            output = subprocess.run(
                [sys.executable, 'tools/replay_compliance.py', str(path), '--require', 'gloves'],
                cwd=ROOT, capture_output=True, text=True, check=True,
            ).stdout
        self.assertIn('falta=guantes', output)


if __name__ == '__main__':
    unittest.main()
