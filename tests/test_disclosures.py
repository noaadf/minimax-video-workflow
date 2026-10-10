import tempfile
import unittest
from pathlib import Path
from workflow.disclosures import normalize
from workflow.media import captions


class DisclosureTests(unittest.TestCase):
    def test_cumulative_duplicate_repairs_do_not_darkenen_label(self):
        rows = [{'start': 18, 'end': 22, 'text': '概念示意'},
                {'start': 22, 'end': 26, 'text': '概念示意'},
                {'start': 18, 'end': 22, 'text': '概念示意'}]
        plan = {'visual_disclosures': rows, 'narration': []}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'captions.ass'
            captions(plan, p)
            self.assertEqual(p.read_text().count('概念示意'), 1)
            self.assertIn('0:00:18.00,0:00:26.00', p.read_text())
            self.assertIn('AI演绎 · 非现场实拍', p.read_text())
        self.assertEqual(len(rows), 3)  # Source evidence remains immutable.

    def test_partial_overlap_merges_only_identical_meaning(self):
        self.assertEqual(normalize([{'start': 1, 'end': 4, 'text': '概念'},
                                    {'start': 3, 'end': 5, 'text': '概念'}]),
                         [{'start': 1, 'end': 5, 'text': '概念'}])
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            normalize([{'start': 1, 'end': 4, 'text': '概念'},
                       {'start': 3, 'end': 5, 'text': '界面仅作演示'}])

    def test_ass_precision_and_gaps(self):
        rows = [{'start': 1, 'end': 2.004, 'text': 'A'},
                {'start': 2.001, 'end': 3, 'text': 'B'},
                {'start': 4, 'end': 5, 'text': 'B'}]
        self.assertEqual(len(normalize(rows)), 3)
        with self.assertRaises(ValueError):
            normalize([{'start': 1.001, 'end': 1.004, 'text': 'A'}])

    def test_invalid_ranges_fail_before_render(self):
        for a, b in [(-1, 2), (1, 31), (1, 1), (float('nan'), 2)]:
            with self.assertRaises(ValueError):
                normalize([{'start': a, 'end': b, 'text': 'A'}])
