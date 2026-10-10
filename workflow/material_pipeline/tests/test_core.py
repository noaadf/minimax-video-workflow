import tempfile
import unittest
from pathlib import Path
from workflow.material_pipeline.core import canonical_url, content_id, deduplicate, local_path, readiness


class IntakeTest(unittest.TestCase):
    def test_tracking_and_fragments_do_not_hide_duplicate_source(self):
        self.assertEqual(canonical_url('https://cas.cn/a?utm_source=x#top'), 'https://cas.cn/a')
        with self.assertRaises(ValueError):
            canonical_url('https://cas.cn/a?token=secret')

    def test_title_is_not_identity(self):
        event = dict(entity='天问二号', action='发射', date='2025-05-29', object='2016 HO3')
        self.assertEqual(content_id(event), content_id({**event, 'title': '换标题'}))
        package = {'content_id': content_id(event), 'sources': [{'url': 'https://cas.cn/a'}],
                   'facts': [{'fingerprint': 'f1', 'text': '完全改写'}]}
        result = deduplicate(package, [{'reference': 'old', 'content_id': content_id(event),
                                       'fact_fingerprints': ['f1']}])
        self.assertEqual(result['status'], 'review_required')
        self.assertIn('same_event', result['matches'][0]['reasons'])

    def test_fact_wording_and_title_cannot_override_canonical_fact(self):
        package = {'content_id': 'new', 'sources': [{'url': 'https://cas.cn/new'}],
                   'facts': [{'fingerprint': 'f1', 'text': '新文案'}]}
        result = deduplicate(package, [{'reference': 'old', 'fact_fingerprints': ['f1']}])
        self.assertIn('same_canonical_fact', result['matches'][0]['reasons'])

    def test_renamed_media_is_detected_by_bytes(self):
        package = {'content_id': 'new', 'sources': [{'url': 'https://cas.cn/new'}],
                   'facts': [{'fingerprint': 'f1', 'text': '新文案'}],
                   'assets': [{'path': 'renamed.mp4', 'sha256': 'a'*64}]}
        result = deduplicate(package, [{'reference': 'old.mp4', 'asset_sha256': ['a'*64]}])
        self.assertIn('same_media_bytes_requires_editorial_review', result['matches'][0]['reasons'])

    def test_path_traversal_and_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            outside = Path(b)/'secret'; outside.write_text('x')
            (Path(a)/'link').symlink_to(outside)
            for path in ('../secret', 'link', str(outside)):
                with self.assertRaises(ValueError): local_path(a, path)

    def test_collection_and_research_do_not_imply_production(self):
        package = {'collection_pending': [], 'production_pending': [],
                   'facts': [{'verification': 'source_supported'}],
                   'sources': [{'published': '2025-05-29', 'date_evidence': '发布时间'}],
                   'shots': [{'status': 'candidate'}], 'transcript': {'status': 'automatic_unreviewed'},
                   'assets': [{'id': 'A1', 'use': 'production', 'rights': {'status': 'unknown'}}]}
        result = readiness(package, {'status': 'clear_in_scanned_scope'})
        self.assertTrue(result['collection_complete']); self.assertTrue(result['research_ready'])
        self.assertFalse(result['production_ready']); self.assertFalse(result['production_approved'])
        self.assertIn('rights_A1', result['blockers'])


if __name__ == '__main__': unittest.main()
