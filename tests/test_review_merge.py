import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from workflow.delivery_policy import assess
from workflow.runner import execute
from workflow.speech_gate import retain_review
from workflow.temporal_qc import retain_exceptions


class ReviewMergeTests(unittest.TestCase):
    def editorial(self, verdict):
        return {'status': 'automatic_review_passed' if verdict == 'pass' else 'review_required',
                'receipt': {'model': 'synthetic', 'request_id': 'retained'},
                'review': {'verdict': verdict, 'findings': [], 'confidence': .9}}

    def speech(self):
        return {'status': 'phonetic_review_required', 'review_required': True,
                'receipt_path': 'synthetic-asr.json'}

    def temporal(self):
        return {'technical_scan_passed': True, 'exceptions': [
            {'kind': 'unplanned_scene_change', 'start': .5, 'reason': 'Synthetic cut'}]}

    def plan(self):
        return {'id': 'film', 'input_sha256': {}, 'quality_profile': 'throughput_v1',
                'shots': [{'id': 'S1', 'duration_seconds': 2}], 'narration': []}

    def assert_rejection_blocks(self, editorial, speech=None):
        # Owner-relaxed 2026-10-06: merge integrity still holds (verdict and receipt
        # retained); a bare rejection is now advisory rather than blocking.
        self.assertEqual(editorial['review']['verdict'], 'reject')
        policy = assess({'passed': True, 'editorial': editorial, 'speech': speech or {}})
        self.assertTrue(policy['ready_for_delivery'])
        self.assertIn('unresolved_rejection', [x['category'] for x in policy['advisories']])
        self.assertEqual(editorial['receipt']['request_id'], 'retained')

    def test_speech_uncertainty_cannot_replace_rejection(self):
        original = self.editorial('reject')
        before = copy.deepcopy(original)
        merged = retain_review(original, self.speech())
        self.assert_rejection_blocks(merged, self.speech())
        self.assertEqual(original, before)
        self.assertEqual(merged['review']['findings'][0]['evidence'], 'synthetic-asr.json')

    def test_temporal_exception_cannot_replace_rejection(self):
        merged = retain_exceptions(self.editorial('reject'), self.temporal(), self.plan())
        # Owner-relaxed 2026-10-06: verdict/receipt retention still holds; the merged
        # temporal finding keeps its own (material) policy weight.
        self.assertEqual(merged['review']['verdict'], 'reject')
        self.assertEqual(merged['receipt']['request_id'], 'retained')
        policy = assess({'passed': True, 'editorial': merged})
        self.assertNotIn('unresolved_rejection', [x['category'] for x in policy['blockers']])
        self.assertEqual(merged['review']['findings'][0]['shot_ids'], ['S1'])

    def test_rejection_survives_both_merge_orders(self):
        for speech_first in (False, True):
            with self.subTest(speech_first=speech_first):
                editorial = self.editorial('reject')
                if speech_first:
                    editorial = retain_review(editorial, self.speech())
                    editorial = retain_exceptions(editorial, self.temporal(), self.plan())
                else:
                    editorial = retain_exceptions(editorial, self.temporal(), self.plan())
                    editorial = retain_review(editorial, self.speech())
                self.assert_rejection_blocks(editorial, self.speech())
                self.assertEqual(len(editorial['review']['findings']), 2)

    def test_advisories_still_allow_pass_and_review(self):
        for verdict in ('pass', 'review'):
            with self.subTest(verdict=verdict):
                merged = retain_exceptions(self.editorial(verdict), self.temporal(), self.plan())
                merged = retain_review(merged, self.speech())
                self.assertEqual(merged['status'], 'review_required')
                self.assertEqual(merged['review']['verdict'], 'review')
                self.assertTrue(assess({'passed': True, 'editorial': merged,
                                        'speech': self.speech()})['ready_for_delivery'])

    def test_no_exceptions_or_homophones_leave_rejection_unchanged(self):
        original = self.editorial('reject')
        self.assertEqual(retain_exceptions(copy.deepcopy(original), {'exceptions': []}, self.plan()), original)
        self.assertEqual(retain_review(original, {'status': 'homophone_spelling_only',
                                                   'review_required': False}), original)

    def test_merging_does_not_repair_a_missing_or_invalid_content_verdict(self):
        for verdict in (None, 'invalid'):
            for merge in ('speech', 'temporal'):
                with self.subTest(verdict=verdict, merge=merge):
                    editorial = self.editorial('pass')
                    editorial['review']['verdict'] = verdict
                    if merge == 'speech':
                        merged = retain_review(editorial, self.speech())
                    else:
                        merged = retain_exceptions(editorial, self.temporal(), self.plan())
                    self.assertEqual(merged['review']['verdict'], verdict)
                    # Owner-relaxed 2026-10-06: invalid verdict evidence is advisory.
                    self.assertTrue(assess({'passed': True, 'editorial': merged})['ready_for_delivery'])

    def test_material_finding_is_retained_and_blocks_after_merge(self):
        original = self.editorial('reject')
        finding = {'category': 'geometry', 'severity': 'high', 'issue': 'Synthetic fusion', 'shot_ids': ['S1']}
        original['review']['findings'] = [finding]
        merged = retain_review(original, self.speech())
        self.assertEqual(merged['review']['verdict'], 'reject')
        self.assertEqual(merged['review']['findings'][0], finding)
        self.assertFalse(assess({'passed': True, 'editorial': merged,
                                'speech': self.speech()})['ready_for_delivery'])

    def test_quality_stage_retains_rejection_through_shot_merge_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps(self.plan()))
            deps = {'compose': {'manifest_path': str(manifest), 'video_path': 'synthetic.mp4',
                                'contact_path': 'synthetic.jpg'},
                    'music': {'audio_path': 'synthetic.wav'},
                    'speech': {'status': 'transcript_match', 'review_required': False}}
            task = {'id': 'film:quality', 'token': 'test', 'stage': 'quality', 'deps': list(deps),
                    'payload': {'plan': self.plan(), 'folder': str(root),
                                'data': {'compose': 'compose', 'music': 'music', 'speech': 'speech'}}}
            queue = Mock()
            queue.results.return_value = deps
            shots = {'status': 'review_required', 'shots': [
                {'shot_id': 'S1', 'review': {'findings': [
                    {'severity': 'low', 'category': 'visual_quality', 'issue': 'Synthetic polish'}]}}]}
            with ExitStack() as stack:
                for target, result in [
                    ('workflow.runner.media.quality', {'passed': True, 'checks': {}}),
                    ('workflow.programmatic_qc.screen_video', {'hard_failed': False, 'checks': {}}),
                    ('workflow.temporal_qc.scan', {'technical_scan_passed': True, 'exceptions': []}),
                    ('workflow.runner.editorial_review', self.editorial('reject')),
                    ('workflow.shot_review.review_film', shots),
                ]:
                    stack.enter_context(patch(target, return_value=result))
                result = execute(queue, task)
            self.assert_rejection_blocks(result['editorial'],
                                         {'status': 'transcript_match', 'review_required': False})
            self.assertTrue(result['delivery_policy']['ready_for_delivery'])
            persisted = json.loads(Path(result['report_path']).read_text())
            self.assertEqual(persisted['editorial']['review']['verdict'], 'reject')
            # Owner-relaxed 2026-10-06: bare rejection is advisory in the persisted policy.
            self.assertTrue(persisted['delivery_policy']['ready_for_delivery'])
            self.assertEqual(result['editorial']['review']['findings'][0]['shot_ids'], ['S1'])
