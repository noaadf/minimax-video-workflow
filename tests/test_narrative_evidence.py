# Adapted from tutorial-workflow handoff tests.
import copy
import unittest
from workflow.narrative_evidence import validate


def sample():
    def shot(sid, role="action"):
        return dict(id=sid, duration=3.0, scene="warehouse", axis="near",
                    actors={"buyer": "navy_right"}, state_in={"boxes": 2}, state_out={"boxes": 2},
                    cut_reason="see strap result", relation={"type": "continuous"},
                    role=role, action_complete_local=2.4)
    return dict(duration=6, shots=[shot("A"), shot("B")],
                voice=[dict(start=.2, end=5, measured_duration=4.8)], minimum_tail=.8,
                music=dict(fade_start=5.1, fade_end=6, source_audio_enabled=False, loop_after_fade=False))


class EvidenceContracts(unittest.TestCase):
    def check_rule(self, plan, rule):
        self.assertIn(rule, [x["rule"] for x in validate(plan)["issues"]])

    def test_valid_structure_does_not_become_approval(self):
        result = validate(sample())
        self.assertEqual(result["status"], "structural_preflight_only")
        self.assertTrue(result["not_an_approval"])

    def test_object_jump(self):
        p=sample();p["shots"][1]["state_in"]={"boxes":0};self.check_rule(p,"STATE_HANDOFF")

    def test_perceivable_ellipsis(self):
        p=sample();p["shots"][1].update(state_in={"boxes":0},state_out={"boxes":0},relation={"type":"ellipsis","cue":"empty to loaded establishes omitted preparation"})
        self.assertEqual(validate(p)["status"],"structural_preflight_only")
        del p["shots"][1]["relation"]["cue"];self.check_rule(p,"ELLIPSIS_CUE")

    def test_return_after_insert_remembers_scene(self):
        p=sample();insert=copy.deepcopy(p["shots"][0]);insert.update(id="I",scene="parts",role="evidence_insert",label="service schematic")
        p["shots"].insert(1,insert);p["duration"]=9;p["music"]["fade_end"]=9
        p["shots"][-1]["state_in"]={"boxes":0};self.check_rule(p,"STATE_HANDOFF")

    def test_axis_crossing(self):
        p=sample();p["shots"][1]["axis"]="far";self.check_rule(p,"SPACE_AXIS")

    def test_action_cut_before_complete(self):
        p=sample();p["shots"][0]["action_complete_local"]=3.1;self.check_rule(p,"TRUNCATED_ACTION")

    def test_missing_action_is_pending(self):
        p=sample();p["shots"][0]["action_complete_local"]=None;self.assertEqual(validate(p)["status"],"evidence_pending")

    def test_observed_box_appearance(self):
        p=sample();p["shots"][0]["observations"]=[dict(local_time=1.5,state={"boxes":3},evidence="review/frame.png")];self.check_rule(p,"OBSERVED_MUTATION")

    def test_audio_effect_order(self):
        p=sample();p["music"]["loop_after_fade"]=True;self.check_rule(p,"MUSIC_EFFECT_ORDER")

    def test_voice_duration_and_tail(self):
        p=sample();p["voice"][0]["end"]=5.8;self.check_rule(p,"VOICE_MEASUREMENT");self.check_rule(p,"ENDING_BREATH")

    def test_duplicate_shot_id_rejected(self):
        p=sample();p["shots"][1]["id"]="A"
        with self.assertRaises(ValueError):validate(p)


if __name__ == "__main__":
    unittest.main()
