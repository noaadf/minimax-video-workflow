import tempfile
import unittest
from pathlib import Path
from workflow.pipeline_coordinator import delivery_wait
from workflow.plan import write_json, sha256


class DeliveryWaitTests(unittest.TestCase):
    def test_exact_blockers_survive_and_stale_evidence_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / 'manifest.json'
            write_json(manifest, {'version': 1})
            request = {'state': 'awaiting_quality_review', 'manifest_path': str(manifest),
                       'manifest_sha256': sha256(manifest)}
            path = root / 'deployment_request.json'
            self.assertEqual(delivery_wait(request, path)['detail']['reason'], 'quality_gate_diagnostic_missing')
            blockers = [{'category': 'story_action_unverified', 'issue': 'S02 motion not verified'}]
            write_json(root / 'quality_gate_diagnostic.json',
                       {'manifest_sha256': sha256(manifest), 'policy': {'blockers': blockers}})
            result = delivery_wait(request, path)
            self.assertEqual(result['state'], 'awaiting_quality_review')
            self.assertEqual(result['detail']['blockers'], blockers)
            self.assertFalse(result['detail']['automatic_regeneration_allowed'])
            write_json(manifest, {'version': 2})
            stale = delivery_wait(request, path)
            self.assertEqual(stale['detail']['reason'], 'quality_gate_diagnostic_stale')
            self.assertNotIn('blockers', stale['detail'])

    def test_export_completion_remains_unaccepted(self):
        self.assertEqual(delivery_wait({'state': 'completed'}, '/tmp/request.json')['state'],
                         'awaiting_film_acceptance')
