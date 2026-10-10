import tempfile
import unittest
from pathlib import Path

from workflow.dashboard_actions import ActionError
from workflow.plan import sha256, write_json
from workflow.product_approval import approve
from workflow.products import products
from workflow.queue import Queue


class ProductQualityBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'output' / 'production'
        self.queue = Queue(self.root / 'queue.sqlite')
        self.folder = self.root / 'film'
        self.folder.mkdir()
        self.video = self.folder / 'video.mp4'
        self.video.write_bytes(b'reviewed video')
        self.checksum = sha256(self.video)
        self.quality = {'passed': True, 'delivery_policy':
                        {'ready_for_delivery': True, 'blockers': []}}
        write_json(self.folder / 'quality.json', self.quality)

    def complete(self, quality=None, export=None, jid='film'):
        self.queue.submit(jid, jid, {'id': jid, 'title': 'Synthetic film'}, [
            {'id': jid + ':quality', 'stage': 'quality', 'payload': {'data': {}}, 'deps': []},
            {'id': jid + ':export', 'stage': 'export', 'payload': {'data': {}}, 'deps': [jid + ':quality']},
        ])
        task = self.queue.claim(['cpu'], job_ids={jid}, stages={'quality'})
        self.queue.finish(task, result=self.quality if quality is None else quality)
        task = self.queue.claim(['cpu'], job_ids={jid}, stages={'export'})
        receipt = {'video_path': str(self.video), 'sha256': {str(self.video): self.checksum}}
        self.queue.finish(task, result=receipt if export is None else export)

    def item(self, jid='film'):
        return next(p for p in products(self.root, []) if p['id'] == jid)

    def test_matching_export_and_quality_receipts_allow_approval(self):
        self.complete()
        self.assertTrue(self.item()['can_approve'])
        self.assertEqual(approve(self.root, 'film', self.checksum)['status'], 'approved')

    def test_changed_video_cannot_inherit_old_pass_or_be_approved(self):
        self.complete()
        self.video.write_bytes(b'changed unreviewed video')
        self.assertFalse(self.item()['can_approve'])
        with self.assertRaises(ActionError):
            approve(self.root, 'film', sha256(self.video))

    def test_unbound_quality_sidecar_cannot_approve_catalog_only_video(self):
        write_json(self.root / 'batch/latest_review_bundle.json', {'videos': [
            {'id': 'film', 'title': 'Synthetic film', 'video': str(self.video)}]})
        self.assertFalse(self.item()['can_approve'])

    def test_sidecar_cannot_override_queue_quality_rejection(self):
        self.complete(quality={'passed': True, 'delivery_policy':
                      {'ready_for_delivery': False, 'blockers': [{'category': 'geometry'}]}})
        self.assertFalse(self.item()['can_approve'])

    def test_successful_export_without_video_hash_is_insufficient(self):
        self.complete(export={'video_path': str(self.video)})
        self.assertFalse(self.item()['can_approve'])

    def test_quality_from_another_job_cannot_supply_fallback(self):
        self.complete(jid='other')
        self.complete(quality={'passed': False}, jid='film')
        self.assertTrue(self.item('other')['can_approve'])
        self.assertFalse(self.item()['can_approve'])

    def test_identical_video_copy_keeps_sha_bound_assessment(self):
        self.complete()
        copied = self.root / 'copies/video.mp4'
        copied.parent.mkdir()
        copied.write_bytes(self.video.read_bytes())
        write_json(self.root / 'batch/latest_review_bundle.json', {'videos': [
            {'id': 'film', 'title': 'Synthetic film', 'video': str(copied)}]})
        self.assertTrue(self.item()['can_approve'])

    def test_explicit_sha_bound_acceptance_preserves_legacy_path(self):
        write_json(self.root / 'batch/latest_review_bundle.json', {'videos': [
            {'id': 'film', 'title': 'Synthetic film', 'video': str(self.video)}]})
        write_json(self.root / 'batch/throughput_policy_acceptance.json', {'items': [
            {'video_sha256': self.checksum, 'ready_for_delivery': True, 'blockers': []}]})
        self.assertTrue(self.item()['can_approve'])
        self.video.write_bytes(b'changed')
        self.assertFalse(self.item()['can_approve'])
