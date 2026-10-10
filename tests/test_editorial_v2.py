import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow.editorial import review_ready
from workflow.plan import generate, validate


def example():
    return {"schema_version": 1, "id": "editorial_example", "template": "science_reveal",
            "pacing_policy": "editorial_v2", "duration_seconds": 18, "aspect_ratio": "9:16",
            "facts": [{"id": "F1", "text": "confirmed fact", "source_url": "https://example.org/source"}],
            "continuity_bible": "same subject", "comment_prompt": "Which matters?", "cover_title": "测试",
            "music": {"path": "music.wav", "license_note": "original"},
            "narration": [{"start": .2, "end": 5, "text": "提出一个具体问题。", "fact_ids": []}],
            "editorial": {"central_question": "为什么会这样？", "beats": [
                {"id": "B1", "purpose": "建立问题", "contribution": "展示问题"},
                {"id": "B2", "purpose": "解释", "contribution": "展示原因"},
                {"id": "B3", "purpose": "回扣", "contribution": "展示结果"}]},
            "shots": [{"id": f"S{i+1}", "camera": "same view", "duration_seconds": 6,
                       "prompt": "one scene", "purpose": "communicate the beat", "beat_ids": [f"B{i+1}"],
                       "duration_reason": "let the action finish", "fact_ids": ["F1"]} for i in range(3)]}


class EditorialTests(unittest.TestCase):
    def test_three_shots_same_view_valid_when_story_is_covered(self):
        validate(example())

    def test_missing_or_bad_beat_contracts_rejected(self):
        edits = [lambda p: p.pop("editorial"),
                 lambda p: p["editorial"].update(central_question=""),
                 lambda p: p["editorial"]["beats"].append(copy.deepcopy(p["editorial"]["beats"][0])),
                 lambda p: p["editorial"]["beats"][0].update(contribution=""),
                 lambda p: p["shots"][0].update(beat_ids=["unknown"]),
                 lambda p: p["shots"][0].update(beat_ids=["B1", "B1"]),
                 lambda p: p["shots"][0].update(purpose=""),
                 lambda p: p["shots"].pop()]
        for edit in edits:
            p = example()
            edit(p)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                validate(p)

    def test_fact_and_total_constraints_remain(self):
        for edit in [lambda p: p["shots"][0].update(fact_ids=["fabricated"]),
                     lambda p: p.update(duration_seconds=19)]:
            p = example()
            edit(p)
            with self.assertRaises(ValueError):
                validate(p)

    def test_legacy_quotas_remain(self):
        p = example()
        p.pop("pacing_policy")
        with self.assertRaisesRegex(ValueError, "six"):
            validate(p)

    def test_writer_prompt_and_policy_are_consistent(self):
        p = example()
        brief = {k: p[k] for k in ["id", "template", "facts", "music", "pacing_policy"]}
        with tempfile.TemporaryDirectory() as d, patch("workflow.llm.ask_json", return_value=(p, {})) as ask, \
                patch("workflow.script_attribution.record"), patch("workflow.engagement.enrich"):
            result = generate(brief, Path(d) / "plan.json")
            prompt = ask.call_args.args[0]
            self.assertIn("no quota", prompt)
            self.assertNotIn("At least 6 shots", prompt)
            self.assertNotIn("Use 5-6 complete", prompt)
            self.assertEqual(result["pacing_policy"], "editorial_v2")

    def test_review_is_bound_to_actual_picture_and_edl(self):
        review = {"preview_sha256": "picture", "edl_sha256": "edit", "decision": "continue",
                  "watched_at_original_speed": True, "listened_to_full_audio": True, "notes": "Clear."}
        self.assertTrue(review_ready(review, "picture", "edit"))
        self.assertFalse(review_ready(review, "changed", "edit"))
        self.assertFalse(review_ready(review, "picture", "changed"))
        review["listened_to_full_audio"] = False
        self.assertFalse(review_ready(review, "picture", "edit"))
