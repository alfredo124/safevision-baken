"""Motor con puntos clave de pose (Tarea 1B). Datos simulados, sin YOLO."""
import tests  # noqa: F401  Primero: BD y capturas temporales (nunca las reales), se ejecute como se ejecute.
import unittest

from app.modules.reglas_lab.compliance import (
    ComplianceConfig, ComplianceEngine, ComplianceState, EquipmentDetection, FrameObservation, PersonDetection,
    observation_from_record,
)
from tests.test_compliance import BOOTS, COAT, GLOVES, GOGGLES, run, state_of

BOX = (100.0, 100.0, 200.0, 400.0)  # persona de 300 px de alto
OTHER = (400.0, 100.0, 500.0, 400.0)
HIDDEN = 0.1  # confianza de un punto oculto


def skeleton(box=BOX, wrists=(0.9, 0.9), face=0.9, torso=0.9):
    """17 puntos COCO en posiciones plausibles dentro de la caja."""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    points = [(x1 + w / 2, y1 + 0.08 * h, face)] * 5  # nariz, ojos, orejas
    points += [(x1 + 0.25 * w, y1 + 0.2 * h, torso), (x1 + 0.75 * w, y1 + 0.2 * h, torso)]  # hombros
    points += [(x1 + 0.2 * w, y1 + 0.35 * h, 0.9), (x1 + 0.8 * w, y1 + 0.35 * h, 0.9)]  # codos
    points += [(x1 + 0.15 * w, y1 + 0.5 * h, wrists[0]), (x1 + 0.85 * w, y1 + 0.5 * h, wrists[1])]  # muñecas
    points += [(x1 + 0.35 * w, y1 + 0.55 * h, torso), (x1 + 0.65 * w, y1 + 0.55 * h, torso)]  # caderas
    points += [(x1 + w / 2, y1 + 0.8 * h, 0.9)] * 4  # rodillas y tobillos
    return tuple(points)


def wrist_glove(box=BOX, side=0):
    x, y = skeleton(box)[9 + side][:2]
    return EquipmentDetection('gloves', (x - 8, y - 8, x + 8, y + 8), 0.8)


def face_goggles(box=BOX):
    x, y = skeleton(box)[0][:2]
    return EquipmentDetection('goggles', (x - 20, y - 6, x + 20, y + 6), 0.8)


def torso_coat(box=BOX):
    x1, y1, x2, y2 = box
    return EquipmentDetection('lab_coat', (x1, y1 + 0.15 * (y2 - y1), x2, y1 + 0.9 * (y2 - y1)), 0.8)


def posed(track_id=1, box=BOX, **kwargs):
    return lambda now: [PersonDetection(track_id, box, 0.9, keypoints=skeleton(box, **kwargs))]


class PoseRules(unittest.TestCase):
    def test_full_equipment_is_compliant(self):
        engine = ComplianceEngine([GLOVES, GOGGLES, COAT])
        everything = lambda now: [wrist_glove(side=0), wrist_glove(side=1), face_goggles(), torso_coat()]
        events, result = run(engine, 20, posed(), everything)
        self.assertEqual(events, [])
        for equipment_id in ('guantes', 'lentes', 'chaleco'):
            self.assertEqual(state_of(result, 1, equipment_id), ComplianceState.COMPLIANT)

    def test_hidden_hands_never_create_glove_incident(self):
        engine = ComplianceEngine([GLOVES])
        events, result = run(engine, 60, posed(wrists=(HIDDEN, HIDDEN)))
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'guantes'), ComplianceState.NOT_VERIFIABLE)

    def test_visible_bare_hand_creates_incident_within_period(self):
        engine = ComplianceEngine([GLOVES])
        events, _ = run(engine, 20, posed())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].equipment_id, 'guantes')
        self.assertLessEqual(events[0].timestamp, ComplianceConfig().window_seconds)

    def test_one_bare_hand_counts_as_missing(self):
        engine = ComplianceEngine([GLOVES])
        events, _ = run(engine, 20, posed(), lambda now: [wrist_glove(side=0)])
        self.assertEqual(len(events), 1)

    def test_one_hidden_hand_and_one_gloved_hand_is_compliant(self):
        engine = ComplianceEngine([GLOVES])
        events, result = run(engine, 20, posed(wrists=(0.9, HIDDEN)), lambda now: [wrist_glove(side=0)])
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'guantes'), ComplianceState.COMPLIANT)

    def test_glove_far_from_wrist_does_not_count(self):
        x1, y1, _, _ = BOX
        far = EquipmentDetection('gloves', (x1 + 40, y1 + 280, x1 + 60, y1 + 300), 0.8)  # cerca de los pies
        events, _ = run(ComplianceEngine([GLOVES]), 20, posed(), lambda now: [far])
        self.assertEqual(len(events), 1)

    def test_neighbour_glove_is_not_assigned_to_bare_handed_person(self):
        engine = ComplianceEngine([GLOVES])
        people = lambda now: posed(1, BOX)(now) + posed(2, OTHER)(now)
        gloves = lambda now: [wrist_glove(OTHER, 0), wrist_glove(OTHER, 1)]
        events, result = run(engine, 20, people, gloves)
        self.assertEqual([event.track_id for event in events], [1])
        self.assertEqual(state_of(result, 2, 'guantes'), ComplianceState.COMPLIANT)

    def test_hidden_face_does_not_evaluate_goggles(self):
        events, result = run(ComplianceEngine([GOGGLES]), 30, posed(face=HIDDEN))
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'lentes'), ComplianceState.NOT_VERIFIABLE)

    def test_missing_goggles_with_visible_face_alerts(self):
        events, _ = run(ComplianceEngine([GOGGLES]), 20, posed())
        self.assertEqual([event.equipment_id for event in events], ['lentes'])

    def test_coat_must_cover_torso(self):
        x1, y1, x2, _ = BOX
        small = EquipmentDetection('lab_coat', (x1, y1 + 30, x2, y1 + 70), 0.8)  # solo los hombros
        events, _ = run(ComplianceEngine([COAT]), 20, posed(), lambda now: [small])
        self.assertEqual(len(events), 1)
        events, _ = run(ComplianceEngine([COAT]), 20, posed(), lambda now: [torso_coat()])
        self.assertEqual(events, [])

    def test_hidden_torso_does_not_evaluate_coat(self):
        events, result = run(ComplianceEngine([COAT]), 30, posed(torso=HIDDEN))
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'chaleco'), ComplianceState.NOT_VERIFIABLE)

    def test_brief_occlusion_keeps_progress(self):
        # 8 s visible sin guantes, 2 s oculto, luego visible: alerta sin reiniciar desde cero.
        def people(now):
            hidden = 8 <= now < 10
            return posed(wrists=(HIDDEN, HIDDEN) if hidden else (0.9, 0.9))(now)
        events, _ = run(ComplianceEngine([GLOVES]), 20, people)
        self.assertEqual(len(events), 1)
        self.assertLessEqual(events[0].timestamp, 15.0)

    def test_boots_stay_manual(self):
        events, result = run(ComplianceEngine([BOOTS]), 30, posed())
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'botas'), ComplianceState.NOT_VERIFIABLE)


class PoseRecordFormat(unittest.TestCase):
    def test_pose_record_is_parsed(self):
        record = {
            'timestamp': 1.5, 'frame_size': [640, 360],
            'persons': [{'track_id': 3, 'box': list(BOX), 'conf': 0.8, 'keypoints': [list(p) for p in skeleton()]}],
            'equipment': [{'name': 'gloves', 'box': [1, 2, 3, 4], 'conf': 0.7},
                          {'name': 'person', 'box': [1, 2, 3, 4], 'conf': 0.9}],
        }
        observation = observation_from_record(record)
        self.assertEqual(len(observation.persons), 1)
        self.assertEqual(len(observation.persons[0].keypoints), 17)
        self.assertEqual([item.name for item in observation.equipment], ['gloves'])  # person de best.pt fuera


if __name__ == '__main__':
    unittest.main()


class UpperTorsoTest(unittest.TestCase):
    """Persona sentada o tras una mesa: hombros visibles, caderas no."""

    def _seated(self, **kwargs):
        points = list(skeleton(**kwargs))
        points[11] = (points[11][0], points[11][1], 0.1)  # caderas ocultas
        points[12] = (points[12][0], points[12][1], 0.1)
        return lambda now: [PersonDetection(1, BOX, 0.9, keypoints=tuple(points))]

    def test_coat_evaluated_without_hips(self):
        events, _ = run(ComplianceEngine([COAT]), 20, self._seated())
        self.assertEqual([event.equipment_id for event in events], ['chaleco'])
        events, result = run(ComplianceEngine([COAT]), 20, self._seated(), lambda now: [torso_coat()])
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'chaleco'), ComplianceState.COMPLIANT)

    def test_fallback_can_be_disabled(self):
        from dataclasses import replace
        engine = ComplianceEngine([COAT], replace(ComplianceConfig(), coat_upper_torso_ratio=0.0))
        events, result = run(engine, 20, self._seated())
        self.assertEqual(events, [])
        self.assertEqual(state_of(result, 1, 'chaleco'), ComplianceState.NOT_VERIFIABLE)


class SlowAnalysisTest(unittest.TestCase):
    """Con 3 cámaras en CPU cada una se analiza ~0.3 veces/s: el motor debe seguir alertando."""

    def _states(self, period, seconds=40):
        engine = ComplianceEngine([GLOVES])
        timeline, now = [], 0.0
        while now < seconds:
            result = engine.update(FrameObservation(now, posed()(now), [], (640, 480)))
            status = result.statuses[0]
            timeline.append((now, status.state, status.progress, bool(result.events)))
            now += period
        return timeline

    def test_alerts_with_one_analysis_every_three_seconds(self):
        timeline = self._states(3.3)
        alert_times = [t for t, _, _, fired in timeline if fired]
        self.assertEqual(len(alert_times), 1, 'Debe alertar una vez')
        self.assertLessEqual(alert_times[0], 15.0)

    def test_full_progress_only_when_alert_fires(self):
        for period in (0.5, 1.0, 3.3):
            for now, state, progress, fired in self._states(period):
                if state is ComplianceState.AT_RISK:
                    self.assertLess(progress, 1.0, f'100 % sin alerta (periodo {period} s, t={now})')
