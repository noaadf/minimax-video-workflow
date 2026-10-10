"""Run with: python -B -m unittest discover -s tools/motion_graphics -p 'test_*.py'."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from renderer import Canvas, Renderer
from spec_schema import SpecError, frame_count, freeze_time, load_spec, validate_spec

DEMO = Path(__file__).parent / "demo"


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.spec = load_spec(DEMO / "stat_highlight.json")

    def reject(self, spec, pattern):
        with self.assertRaisesRegex(SpecError, pattern):
            validate_spec(spec)

    def test_missing_contract_fields(self):
        for key in ("shot_id", "duration_seconds", "layout", "title", "reveals"):
            with self.subTest(key=key):
                candidate = deepcopy(self.spec)
                del candidate[key]
                self.reject(candidate, "missing required field")

    def test_missing_kind_specific_fields(self):
        for index, key in ((1, "text"), (2, "left"), (3, "to"), (3, "suffix")):
            with self.subTest(index=index, key=key):
                candidate = deepcopy(self.spec)
                del candidate["reveals"][index][key]
                self.reject(candidate, "missing required field")

    def test_overlap_and_duplicate_persistent_slot(self):
        self.spec["reveals"][1]["t"] = 0.3
        self.reject(self.spec, "time overlap")
        self.spec["reveals"][1]["t"] = 0.5
        self.spec["reveals"].append({"t": 5.5, "kind": "stat", "text": "重复"})
        self.reject(self.spec, "slot overlaps")

    def test_final_hold_and_duration_overrun(self):
        for start in (7.5, 8.4):
            self.spec["reveals"][-1]["t"] = start
            self.reject(self.spec, "final-hold boundary")

    def test_negative_nonfinite_and_boolean_time(self):
        for value in (-1, float("nan"), float("inf"), True, "1"):
            with self.subTest(value=value):
                self.spec["reveals"][0]["t"] = value
                self.reject(self.spec, "finite number")

    def test_invalid_layout_and_wrong_kind(self):
        self.spec["layout"] = "pie"
        self.reject(self.spec, "spec.layout")
        self.spec["layout"] = "process"
        self.reject(self.spec, "unsupported")

    def test_zero_comparison_and_negative_trend(self):
        for side in ("left", "right"):
            self.spec["reveals"][2][side]["value"] = 0
        self.reject(self.spec, "nonzero")
        candidate = load_spec(DEMO / "trend.json")
        next(r for r in candidate["reveals"] if r["kind"] == "trend")["points"][0]["value"] = -2
        self.reject(candidate, "finite number")

    def test_process_capacity(self):
        candidate = load_spec(DEMO / "process.json")
        candidate["reveals"].insert(-1, {"t": 5.2, "kind": "step", "title": "第四步", "text": "超出版式容量"})
        self.reject(candidate, "at most 3")

    def test_unsorted_reveals_are_valid(self):
        self.spec["reveals"].reverse()
        validate_spec(self.spec)

    def test_duration_rounds_to_nearest_frame(self):
        self.assertEqual(frame_count(self.spec), 199)
        self.assertLess(abs(frame_count(self.spec) / 24 - 8.296), 1 / 48)

    def test_bad_emphasis_type_is_a_schema_error(self):
        self.spec["reveals"][1]["emphasis"] = []
        self.reject(self.spec, "emphasis")

    def test_precise_data_is_not_silently_rounded(self):
        self.spec["reveals"][3]["to"] = 40.123
        self.reject(self.spec, "two decimal places")

    def test_implicit_title_also_reserves_entrance_time(self):
        self.spec["reveals"].pop(0)
        self.spec["reveals"][0]["t"] = 0.2
        self.reject(self.spec, "time overlap")

    def test_cli_preserves_existing_output_and_reports_bad_input(self):
        root = Path(__file__).parent
        with tempfile.TemporaryDirectory(dir=root) as directory:
            output = Path(directory) / "existing.mp4"
            output.write_bytes(b"existing content")
            completed = subprocess.run([sys.executable, "-B", str(root / "renderer.py"),
                                        str(DEMO / "stat_highlight.json"), str(output)],
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("refusing to overwrite", completed.stderr)
            self.assertEqual(output.read_bytes(), b"existing content")
            bad = Path(directory) / "bad.json"
            bad.write_text('{"layout":"trend"}', encoding="utf-8")
            unused = Path(directory) / "unused.mp4"
            completed = subprocess.run([sys.executable, "-B", str(root / "renderer.py"), str(bad), str(unused)],
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("missing required field", completed.stderr)
            self.assertFalse(unused.exists())

    def test_only_declared_comparison_group_may_overlap(self):
        spec = load_spec(DEMO / "comparison.json")
        validate_spec(spec)
        spec["reveals"].reverse()
        validate_spec(spec)
        result = next(r for r in spec["reveals"] if r["kind"] == "number_roll")
        result.pop("sync_with")
        self.reject(spec, "time overlap")
        result["sync_with"] = "comparison"
        result["t"] += 0.1
        self.reject(spec, "share comparison start time")
        result["t"] -= 0.1
        result["sync_with"] = "stat"
        self.reject(spec, "only comparison")
        result["sync_with"] = "comparison"
        spec["reveals"].append({"t": 2.27, "kind": "source_note", "text": "不允许侵占同步组尾部"})
        spec["reveals"] = [r for r in spec["reveals"] if r["kind"] != "source_note" or r["t"] == 2.27]
        self.reject(spec, "time overlap")


class RenderingTests(unittest.TestCase):
    @staticmethod
    def gold(image):
        rgb = np.asarray(image).astype(np.int16)
        return (rgb[..., 0] > 140) & (rgb[..., 1] > 100) & (rgb[..., 0] > rgb[..., 1]) & (rgb[..., 1] > rgb[..., 2] + 20)

    def test_trend_ten_percent_has_growing_line(self):
        renderer = Renderer(load_spec(DEMO / "trend.json"))
        start = renderer.by_kind["trend"]["t"]
        right_edges = []
        for t in (19 / 24, start + 0.6, start + 1.2):
            mask = self.gold(renderer.frame(t))[326:556, 100:460]
            self.assertGreater(mask.sum(), 50)
            right_edges.append(np.where(mask)[1].max() + 100)
        self.assertTrue(190 < right_edges[0] < 240, right_edges)
        self.assertTrue(right_edges[0] < right_edges[1] < right_edges[2], right_edges)

    def test_comparison_result_enters_with_cards_and_is_complete_at_half(self):
        renderer = Renderer(load_spec(DEMO / "comparison.json"))
        start = renderer.by_kind["comparison"]["t"]
        self.assertEqual(start, renderer.by_kind["number_roll"]["t"])
        first_frame = renderer.frame((int(start * 24) + 1) / 24)
        # Digits are already present in both the first card and the bottom result.
        rgb = np.asarray(first_frame)
        self.assertGreater((rgb[410:520, 70:240].min(axis=2) > 130).sum(), 100)
        self.assertGreater(self.gold(first_frame)[710:818, 70:180].sum(), 100)
        completed = np.asarray(renderer.frame(start + 1.1))[640:818, 48:492]
        half = np.asarray(renderer.frame(4.0))[640:818, 48:492]
        np.testing.assert_array_equal(half, completed)

    def test_first_visible_number_frame_starts_at_zero(self):
        for name in ("stat_highlight", "comparison", "trend"):
            renderer = Renderer(load_spec(DEMO / f"{name}.json"))
            reveal = renderer.by_kind["number_roll"]
            first_time = (int(reveal["t"] * 24) + 1) / 24
            original = Canvas.text
            with patch.object(Canvas, "text", autospec=True, side_effect=original) as text:
                renderer.frame(first_time)
            numbers = [call.args[1] for call in text.call_args_list
                       if call.kwargs.get("numeric") and 600 <= call.args[3] < 829]
            self.assertIn("0", numbers, name)

    def test_endpoint_number_is_inside_chart_even_for_wide_values(self):
        for value in (94, 9999):
            spec = load_spec(DEMO / "trend.json")
            next(r for r in spec["reveals"] if r["kind"] == "trend")["points"][-1]["value"] = value
            image = np.asarray(Renderer(spec).frame(3.0))
            white = image.min(axis=2) > 150
            self.assertGreater(white[326:380, 380:456].sum(), 5)
            self.assertEqual(white[290:326, 380:493].sum(), 0)
            self.assertEqual(white[326:555, 457:493].sum(), 0)

    def test_result_panels_anchor_lower_third(self):
        for name in ("stat_highlight", "comparison"):
            renderer = Renderer(load_spec(DEMO / f"{name}.json"))
            image = renderer.frame(renderer.freeze)
            self.assertGreater(self.gold(image)[690:818, 65:400].sum(), 1500)

    def test_all_layouts_render_and_final_half_second_is_frozen(self):
        for name in ("stat_highlight", "comparison", "process", "trend"):
            with self.subTest(layout=name):
                spec = load_spec(DEMO / f"{name}.json")
                renderer = Renderer(spec)
                image = renderer.frame(spec["duration_seconds"] * 0.5)
                self.assertEqual(image.size, (540, 960))
                self.assertEqual(image.mode, "RGB")
                self.assertGreater(np.asarray(image).std(), 10)
                held = renderer.frame(freeze_time(spec)).tobytes()
                for frame in range(frame_count(spec) - 12, frame_count(spec)):
                    self.assertEqual(renderer.frame(frame / 24).tobytes(), held)

    def test_reveal_changes_pixels_and_is_deterministic(self):
        renderer = Renderer(load_spec(DEMO / "stat_highlight.json"))
        before = renderer.frame(1.4).tobytes()
        after = renderer.frame(2.5).tobytes()
        self.assertNotEqual(before, after)
        self.assertEqual(after, renderer.frame(2.5).tobytes())


if __name__ == "__main__":
    unittest.main()
