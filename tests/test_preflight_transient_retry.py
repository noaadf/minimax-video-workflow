"""Preflight fault isolation: transient review-API failures retry with archiving;
content failures isolate; the retry budget is bounded."""
import json
import tempfile
import unittest
from pathlib import Path
from workflow.core.failures import WorkflowFailure
from workflow.storyboard_batch import (
    archive_reviewable, transient_failure, MAX_TRANSIENT_RETRIES)


class TransientFailureTest(unittest.TestCase):
    def test_provider_reasons_classified_transient(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / 'keyframe_review.json').write_text(json.dumps(
                {'status': 'review_required', 'reason': 'review API unavailable or invalid response',
                 'error_type': 'TimeoutError', 'failure': WorkflowFailure('provider_timeout').record()}))
            self.assertTrue(transient_failure(out))

    def test_content_verdict_not_transient(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / 'keyframe_review.json').write_text(json.dumps(
                {'status': 'review_required',
                 'review': {'verdict': 'review', 'findings': [{'severity': 'medium'}]}}))
            self.assertFalse(transient_failure(out))

    def test_story_review_bare_runtime_error_is_terminal(self):
        # W1 contract: bare RuntimeError is terminal; cloud transport has its own code.
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / 'story_review.json').write_text(json.dumps(
                {'status': 'failed', 'error_type': 'RuntimeError'}))
            self.assertFalse(transient_failure(out))

    def test_story_review_schema_failure_is_content(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / 'story_review.json').write_text(json.dumps(
                {'status': 'failed', 'error_type': 'ValueError'}))
            self.assertFalse(transient_failure(out))

    def test_missing_record_not_transient(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(transient_failure(Path(d)))


class ArchiveRetryableTest(unittest.TestCase):
    def test_archives_render_blockers_keeps_reusable_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / 'animatic').mkdir()
            (out / 'animatic' / 'edl.json').write_text('{}')
            (out / 'keyframe_review.json').write_text('{}')
            (out / 'keyframe_contact.jpg').write_bytes(b'jpg')
            (out / 'story_review.json').write_text('{}')   # reusable (sha-bound)
            (out / 'narration.wav').write_bytes(b'wav')    # reusable (digest-cached)
            archive_reviewable(out, 2)
            self.assertFalse((out / 'animatic').exists())
            self.assertFalse((out / 'keyframe_review.json').exists())
            self.assertFalse((out / 'keyframe_contact.jpg').exists())
            self.assertFalse((out / 'story_review.json').exists())  # retry re-runs it
            self.assertTrue((out / 'retry_archive_2' / 'animatic' / 'edl.json').exists())
            self.assertTrue((out / 'retry_archive_2' / 'story_review.json').exists())
            self.assertTrue((out / 'narration.wav').exists())

    def test_budget_is_bounded(self):
        self.assertEqual(MAX_TRANSIENT_RETRIES, 3)


if __name__ == '__main__':
    unittest.main()
