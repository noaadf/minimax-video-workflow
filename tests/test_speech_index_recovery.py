import json
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow.plan import digest, write_json
from tests import test_speech_gate as fixtures


class SpeechIndexRecoveryTests(unittest.TestCase):
    setUp = fixtures.SpeechGateTests.setUp
    save = fixtures.SpeechGateTests.save
    run_screen = fixtures.SpeechGateTests.run_screen
    configure_worker = fixtures.SpeechGateTests.configure_worker

    def cache(self):
        return Path(self.cfg['cache_root']) / digest({
            'video_sha256': self.report['binding']['video_sha256'],
            'expected': self.report['expected'], 'model': self.cfg['model_id'],
            'model_files': self.cfg['model_files']})

    def assert_unassessed(self, result):
        self.assertEqual(result['status'], 'speech_not_assessed')
        self.assertTrue(result['review_required'])
        self.assertFalse(result['content_approved'])
        self.assertEqual(json.loads((self.root/'screen/report.json').read_text()), result)

    def test_empty_existing_cache_preserves_attempt_without_new_worker(self):
        self.configure_worker()
        cache = self.cache(); cache.mkdir(parents=True)
        write_json(cache/'attempt.json', {'state': 'started', 'automatic_retry': False})
        write_json(cache/'index.json', {'items': []})
        before = {p.name: p.read_bytes() for p in cache.iterdir()}
        with patch('workflow.speech_gate._bounded_worker') as worker:
            self.assert_unassessed(self.run_screen())
            self.assert_unassessed(self.run_screen())
            worker.assert_not_called()
        self.assertEqual({p.name: p.read_bytes() for p in cache.iterdir()}, before)

    def test_empty_worker_commit_is_reported_and_never_retried(self):
        self.configure_worker()
        for shutdown_error in (False, True):
            with self.subTest(shutdown_error=shutdown_error):
                self.cfg['cache_root'] = str(self.root/f'cache-{shutdown_error}')
                self.save()
                def worker(command, log_path, timeout):
                    dest = Path(command[command.index('--output')+1])
                    write_json(dest/'index.json', {'items': []})
                    if shutdown_error:
                        raise RuntimeError('ASR worker exited -6')
                with patch('workflow.speech_gate._bounded_worker', side_effect=worker) as process:
                    self.assert_unassessed(self.run_screen())
                    self.assert_unassessed(self.run_screen())
                    self.assertEqual(process.call_count, 1)
                self.assertEqual(json.loads((self.cache()/'attempt.json').read_text())['automatic_retry'], False)

    def test_empty_index_committed_before_lock_recheck_is_not_retried(self):
        self.configure_worker()
        def acquire(*args):
            cache = self.cache(); cache.mkdir(parents=True)
            write_json(cache/'index.json', {'items': []})
            write_json(cache/'attempt.json', {'state': 'started'})
        with patch('workflow.speech_gate.fcntl.flock', side_effect=acquire), \
             patch('workflow.speech_gate._bounded_worker') as worker:
            self.assert_unassessed(self.run_screen())
            worker.assert_not_called()

    def test_malformed_index_shapes_fail_closed_and_preserve_evidence(self):
        self.configure_worker()
        cache = self.cache(); cache.mkdir(parents=True)
        for value in ([], None, {'items': None}, {'items': {}}, {'items': [None]},
                      {'items': ['invalid']}, {'items': [{'binding': []}]},
                      {'items': [{'binding': None}]}):
            with self.subTest(index=value):
                write_json(cache/'index.json', value)
                original = (cache/'index.json').read_bytes()
                with patch('workflow.speech_gate._bounded_worker') as worker:
                    self.assert_unassessed(self.run_screen())
                    worker.assert_not_called()
                self.assertEqual((cache/'index.json').read_bytes(), original)

    def test_empty_external_inventory_allows_one_valid_new_receipt(self):
        self.configure_worker()
        self.cfg['receipt_indices'] = [str(self.root/'index.json')]; self.save()
        write_json(self.root/'index.json', {'items': []})
        def worker(command, log_path, timeout):
            dest = Path(command[command.index('--output')+1])
            report = json.loads(json.dumps(self.report))
            report['binding']['settings']['model_files'] = self.cfg['model_files']
            report['report_path'] = str(dest/'raw.json')
            write_json(dest/'raw.json', report)
            write_json(dest/'index.json', {'items': [report]})
        with patch('workflow.speech_gate._bounded_worker', side_effect=worker) as process:
            first = self.run_screen(); second = self.run_screen()
        self.assertEqual(first['status'], 'transcript_match')
        self.assertFalse(first['cache_hit']); self.assertTrue(second['cache_hit'])
        self.assertFalse(first['content_approved'])
        self.assertEqual(process.call_count, 1)
