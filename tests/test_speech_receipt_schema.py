import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_speech_gate as fixtures
from tests import test_speech_commit_binding as commit_fixtures
from workflow.delivery_policy import assess
from workflow.plan import write_json
from workflow.runner import execute
from workflow.speech_gate import retain_review, screen


class SpeechReceiptSchemaTests(unittest.TestCase):
    setUp = fixtures.SpeechGateTests.setUp
    save = fixtures.SpeechGateTests.save
    run_screen = fixtures.SpeechGateTests.run_screen
    configure_worker = fixtures.SpeechGateTests.configure_worker
    task = commit_fixtures.SpeechCommitBindingTests.task

    def assert_unassessed(self, result, folder=None):
        self.assertEqual(result['status'], 'speech_not_assessed')
        self.assertTrue(result['review_required'])
        self.assertFalse(result['content_approved'])
        self.assertNotIn('receipt_sha256', result)
        self.assertNotIn('recovered_validated_receipt', result)
        folder = folder or self.root/'screen'
        self.assertEqual(json.loads((folder/'report.json').read_text()), result)

    def test_cached_nonobject_settings_are_reported_without_worker(self):
        for settings in (None, [], 'invalid', 3, False):
            with self.subTest(settings=settings):
                changed = json.loads(json.dumps(self.report))
                changed['binding']['settings'] = settings
                write_json(self.receipt, changed); original = self.receipt.read_bytes()
                with patch('workflow.speech_gate._bounded_worker') as worker, \
                     patch('workflow.speech_gate.classify_phonetics') as compare:
                    self.assert_unassessed(self.run_screen())
                    worker.assert_not_called(); compare.assert_not_called()
                self.assertEqual(self.receipt.read_bytes(), original)

    def test_nonobject_receipts_and_bindings_remain_unassessed(self):
        for bad in (None, [], 'invalid', False):
            for nested in (False, True):
                with self.subTest(value=bad, nested=nested):
                    changed = json.loads(json.dumps(self.report)) if nested else bad
                    if nested:
                        changed['binding'] = bad
                    write_json(self.receipt, changed); original = self.receipt.read_bytes()
                    with patch('workflow.speech_gate._bounded_worker') as worker:
                        self.assert_unassessed(self.run_screen()); worker.assert_not_called()
                    self.assertEqual(self.receipt.read_bytes(), original)

    def test_malformed_transcript_segments_do_not_become_accepted_evidence(self):
        for segments in (None, {}, [None], ['invalid'], [{}], [{'text': 4}]):
            with self.subTest(segments=segments):
                changed = json.loads(json.dumps(self.report)); changed['segments'] = segments
                write_json(self.receipt, changed)
                with patch('workflow.speech_gate._bounded_worker') as worker:
                    self.assert_unassessed(self.run_screen()); worker.assert_not_called()

    def test_malformed_worker_receipt_is_preserved_without_repeat_or_recovery(self):
        self.configure_worker()
        for shutdown_error in (False, True):
            with self.subTest(shutdown_error=shutdown_error):
                self.cfg['cache_root'] = str(self.root/f'cache-{shutdown_error}'); self.save()
                written = []
                def worker(command, log_path, timeout):
                    dest = Path(command[command.index('--output')+1])
                    changed = json.loads(json.dumps(self.report))
                    changed['binding']['settings'] = []
                    changed['report_path'] = str(dest/'raw.json')
                    write_json(dest/'raw.json', changed)
                    write_json(dest/'index.json', {'items': [changed]})
                    written.append((dest, (dest/'raw.json').read_bytes(), (dest/'index.json').read_bytes()))
                    if shutdown_error:
                        raise RuntimeError('ASR worker exited -6')
                with patch('workflow.speech_gate._bounded_worker', side_effect=worker) as process:
                    self.assert_unassessed(self.run_screen())
                    self.assert_unassessed(self.run_screen())
                    self.assertEqual(process.call_count, 1)
                dest, raw_bytes, index_bytes = written[0]
                self.assertEqual((dest/'raw.json').read_bytes(), raw_bytes)
                self.assertEqual((dest/'index.json').read_bytes(), index_bytes)
                self.assertEqual(json.loads((dest/'attempt.json').read_text())['automatic_retry'], False)

    def test_runner_retains_unassessed_report_and_blocks_delivery_with_rejection(self):
        changed = json.loads(json.dumps(self.report)); changed['binding']['settings'] = []
        write_json(self.receipt, changed)
        queue, task, folder = self.task()
        def check(video, plan, dest):
            return screen(video, plan, dest, self.config)
        with patch('workflow.speech_gate.screen', side_effect=check), \
             patch('workflow.speech_gate._bounded_worker') as worker:
            result = execute(queue, task); worker.assert_not_called()
        self.assertEqual(result['status'], 'speech_not_assessed')
        self.assertNotIn('receipt_path', result)
        persisted = json.loads((folder/'receipt.json').read_text())
        self.assertEqual(persisted, result)
        rejection = {'status': 'review_required', 'receipt': {'model': 'test'},
                     'review': {'verdict': 'reject', 'findings': []}}
        editorial = retain_review(rejection, result)
        self.assertEqual(editorial['review']['verdict'], 'reject')
        policy = assess({'passed': True, 'editorial': editorial, 'speech': result})
        self.assertFalse(policy['ready_for_delivery'])
        self.assertIn('audio_check_unavailable', [f['category'] for f in policy['blockers']])
