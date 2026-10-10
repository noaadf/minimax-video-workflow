import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests import test_speech_gate as fixtures
from workflow.plan import sha256, write_json
from workflow.runner import execute, StageError
from workflow.speech_gate import screen


class SpeechCommitBindingTests(unittest.TestCase):
    setUp = fixtures.SpeechGateTests.setUp
    save = fixtures.SpeechGateTests.save

    def task(self, token='one'):
        manifest = self.root/'manifest.json'; write_json(manifest, self.plan)
        queue = Mock()
        queue.results.return_value = {'compose': {
            'manifest_path': str(manifest), 'video_path': str(self.video),
            'sha256': {str(manifest): sha256(manifest), str(self.video): sha256(self.video)}}}
        task = {'id': 'test:speech', 'token': token, 'stage': 'speech', 'deps': ['compose'],
                'payload': {'plan': {'input_sha256': {}}, 'folder': str(self.root),
                            'data': {'compose': 'compose'}}}
        return queue, task, self.root/'attempts/speech'/token

    def screened(self, video, plan, folder):
        return screen(video, plan, folder, self.config)

    def test_receipt_replaced_after_screen_cannot_commit_new_fingerprint(self):
        for kind in ('binding', 'format'):
            with self.subTest(kind=kind):
                self.save(); queue, task, folder = self.task(kind)
                screened_hash = []
                def replace_after_screen(*args):
                    result = self.screened(*args)
                    screened_hash.append(result['receipt_sha256'])
                    if kind == 'binding':
                        changed = json.loads(self.receipt.read_text())
                        changed['binding']['video_sha256'] = 'unreviewed-video'
                        write_json(self.receipt, changed)
                    else:
                        self.receipt.write_text(json.dumps(json.loads(self.receipt.read_text())))
                    self.replacement = self.receipt.read_bytes()
                    return result
                with patch('workflow.speech_gate.screen', side_effect=replace_after_screen), \
                     patch('workflow.speech_gate._bounded_worker') as worker:
                    with self.assertRaisesRegex(StageError, 'speech receipt') as error:
                        execute(queue, task)
                    worker.assert_not_called()
                self.assertFalse(error.exception.retryable)
                self.assertFalse((folder/'receipt.json').exists())
                self.assertEqual(json.loads((folder/'report.json').read_text())['receipt_sha256'], screened_hash[0])
                self.assertNotEqual(sha256(self.receipt), screened_hash[0])
                self.assertEqual(self.receipt.read_bytes(), self.replacement)

    def test_unchanged_screened_receipt_keeps_one_consistent_fingerprint(self):
        queue, task, folder = self.task()
        original = self.receipt.read_bytes()
        with patch('workflow.speech_gate.screen', side_effect=self.screened), \
             patch('workflow.speech_gate._bounded_worker') as worker:
            result = execute(queue, task); worker.assert_not_called()
        self.assertEqual(result['status'], 'transcript_match')
        self.assertFalse(result['content_approved'])
        self.assertEqual(result['receipt_sha256'], result['sha256'][result['receipt_path']])
        self.assertEqual(json.loads((folder/'receipt.json').read_text()), result)
        self.assertEqual(self.receipt.read_bytes(), original)

    def test_declared_receipt_requires_matching_screened_fingerprint(self):
        for snapshot_hash in (None, '', 'wrong-sha'):
            with self.subTest(snapshot_hash=snapshot_hash):
                queue, task, folder = self.task(str(snapshot_hash))
                result = {'status': 'transcript_match', 'review_required': False,
                          'content_approved': False, 'receipt_path': str(self.receipt)}
                if snapshot_hash is not None:
                    result['receipt_sha256'] = snapshot_hash
                with patch('workflow.speech_gate.screen', return_value=result):
                    with self.assertRaisesRegex(StageError, 'speech receipt') as error:
                        execute(queue, task)
                self.assertFalse(error.exception.retryable)
                self.assertFalse((folder/'receipt.json').exists())

    def test_unassessed_screen_without_receipt_remains_unassessed(self):
        queue, task, folder = self.task()
        unavailable = {'status': 'speech_not_assessed', 'review_required': True,
                       'content_approved': False, 'reason': 'Runtime unavailable'}
        def check(*args):
            write_json(Path(args[2])/'report.json', unavailable)
            return dict(unavailable)
        with patch('workflow.speech_gate.screen', side_effect=check):
            result = execute(queue, task)
        self.assertEqual(result['status'], 'speech_not_assessed')
        self.assertTrue(result['review_required']); self.assertFalse(result['content_approved'])
        self.assertNotIn('receipt_path', result)
        self.assertTrue((folder/'receipt.json').is_file())
