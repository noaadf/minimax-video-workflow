"""P0 unblock batch (reorg v2): stage id minting, admission classification,
collect source filtering — the three fixes that reopen the stalled backlog."""
import json
import tempfile
import unittest
from pathlib import Path
from workflow.storyboard_batch import mint_job_id
from scripts.collect_storyboard_image_results import in_batch
from workflow.core.failures import WorkflowFailure
from workflow.storyboard_deployment import admission_disposition, MAX_HELD_TICKS


class MintJobIdTest(unittest.TestCase):
    def test_deterministic_and_collision_free(self):
        taken = set()
        first = mint_job_id('production_abc', 'f' * 64, taken)
        self.assertEqual(first, 'production_abc-r1-ffffffff')
        taken.add(first)
        second = mint_job_id('production_abc', 'f' * 64, taken)
        self.assertEqual(second, 'production_abc-r2-ffffffff')
        # different content -> different id, no bump
        other = mint_job_id('production_abc', '0' * 64, taken)
        self.assertEqual(other, 'production_abc-r1-00000000')


class AdmissionDispositionTest(unittest.TestCase):
    def test_restart_fixable_is_held(self):
        self.assertEqual(admission_disposition(OSError('no such process')), 'held')
        self.assertEqual(admission_disposition(RuntimeError('transport')), 'rejected')
        self.assertEqual(admission_disposition(WorkflowFailure('runtime_not_adopted',
            'Required color/media implementation is not loaded by renderer')), 'held')

    def test_deterministic_refusals_are_rejected(self):
        self.assertEqual(admission_disposition(ValueError(
            'fact source is not archived and explicitly bound')), 'rejected')
        self.assertEqual(admission_disposition(KeyError('backend_file')), 'rejected')

    def test_unknown_is_terminal_and_budget_bounded(self):
        self.assertEqual(admission_disposition(Exception('weird')), 'rejected')
        self.assertEqual(MAX_HELD_TICKS, 3)


class CollectFilterTest(unittest.TestCase):
    def test_foreign_manifests_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inside = root / 'production_x' / 'manifest_r1' / 'manifest.json'
            inside.parent.mkdir(parents=True)
            inside.write_text('{}')
            outside = Path(d).parent / (Path(d).name + '_foreign') / 'm.json'
            outside.parent.mkdir(parents=True)
            outside.write_text('{}')
            self.assertTrue(in_batch(str(inside), root))
            self.assertFalse(in_batch(str(outside), root))
            self.assertFalse(in_batch(str(root / 'missing.json'), root))


if __name__ == '__main__':
    unittest.main()
