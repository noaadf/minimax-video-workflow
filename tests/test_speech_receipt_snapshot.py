import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_speech_gate as fixtures
from workflow.plan import digest, sha256, write_json
from workflow.speech_gate import classify_phonetics, validate_receipt


class SpeechReceiptSnapshotTests(unittest.TestCase):
    setUp = fixtures.SpeechGateTests.setUp
    save = fixtures.SpeechGateTests.save
    run_screen = fixtures.SpeechGateTests.run_screen
    configure_worker = fixtures.SpeechGateTests.configure_worker

    def replace_receipt(self, path, kind='video'):
        report = json.loads(path.read_text())
        if kind == 'video':
            report['binding']['video_sha256'] = 'different-video'
        elif kind == 'transcript':
            report['recognized'] = '竹篮不是竹篮'
            report['segments'] = [{'text': report['recognized']}]
        elif kind == 'format':
            path.write_text(json.dumps(report, ensure_ascii=False))
            self.replacement_bytes = path.read_bytes()
            return
        write_json(path, report)
        self.replacement_bytes = path.read_bytes()

    def classifier_replacing(self, path, kind='video'):
        def compare(expected, recognized):
            result = classify_phonetics(expected, recognized)
            self.replace_receipt(path, kind)
            return result
        return compare

    def assert_changed_evidence_blocked(self, result):
        self.assertEqual(result['status'], 'speech_not_assessed')
        self.assertTrue(result['review_required'])
        self.assertFalse(result['content_approved'])
        self.assertIn('receipt changed', result['reason'])
        self.assertNotIn('receipt_sha256', result)
        self.assertNotIn('recovered_validated_receipt', result)
        self.assertEqual(json.loads((self.root/'screen/report.json').read_text()), result)

    def cache(self):
        return Path(self.cfg['cache_root']) / digest({
            'video_sha256': self.report['binding']['video_sha256'],
            'expected': self.report['expected'], 'model': self.cfg['model_id'],
            'model_files': self.cfg['model_files']})

    def worker_receipt(self, dest):
        report = json.loads(json.dumps(self.report))
        report['binding']['settings']['model_files'] = self.cfg['model_files']
        report['report_path'] = str(dest/'raw.json')
        write_json(dest/'raw.json', report)
        write_json(dest/'index.json', {'items': [report]})

    def test_cached_receipt_replacement_during_comparison_fails_closed(self):
        for kind in ('video', 'transcript', 'format'):
            with self.subTest(kind=kind):
                self.save()
                with patch('workflow.speech_gate.classify_phonetics',
                           side_effect=self.classifier_replacing(self.receipt, kind)), \
                     patch('workflow.speech_gate._bounded_worker') as worker:
                    self.assert_changed_evidence_blocked(self.run_screen())
                    worker.assert_not_called()
                # Detection must preserve the replacement bytes for diagnosis.
                self.assertEqual(self.receipt.read_bytes(), self.replacement_bytes)

    def test_replacement_after_validation_uses_hash_of_the_original_bytes(self):
        def validate(*args):
            report = validate_receipt(*args)
            self.replace_receipt(self.receipt)
            return report
        with patch('workflow.speech_gate.validate_receipt', side_effect=validate), \
             patch('workflow.speech_gate._bounded_worker') as worker:
            self.assert_changed_evidence_blocked(self.run_screen())
            worker.assert_not_called()

    def test_worker_result_replacement_cannot_recover_or_repeat_recognition(self):
        self.configure_worker()
        for shutdown_error in (False, True):
            with self.subTest(shutdown_error=shutdown_error):
                self.cfg['cache_root'] = str(self.root/f'worker-{shutdown_error}'); self.save()
                path = self.cache()/'raw.json'
                def worker(command, log_path, timeout):
                    dest = Path(command[command.index('--output')+1])
                    self.worker_receipt(dest)
                    if shutdown_error:
                        raise RuntimeError('ASR worker exited -6')
                with patch('workflow.speech_gate._bounded_worker', side_effect=worker) as process, \
                     patch('workflow.speech_gate.classify_phonetics', side_effect=self.classifier_replacing(path)):
                    self.assert_changed_evidence_blocked(self.run_screen())
                    self.assertEqual(self.run_screen()['status'], 'speech_not_assessed')
                    self.assertEqual(process.call_count, 1)
                self.assertTrue((self.cache()/'attempt.json').is_file())
                self.assertEqual(json.loads(path.read_text())['binding']['video_sha256'], 'different-video')

    def test_receipt_replacement_during_lock_recheck_fails_closed(self):
        self.configure_worker()
        path = self.cache()/'raw.json'
        def acquire(*args):
            self.cache().mkdir(parents=True)
            self.worker_receipt(self.cache())
        with patch('workflow.speech_gate.fcntl.flock', side_effect=acquire), \
             patch('workflow.speech_gate.classify_phonetics', side_effect=self.classifier_replacing(path)), \
             patch('workflow.speech_gate._bounded_worker') as worker:
            self.assert_changed_evidence_blocked(self.run_screen())
            worker.assert_not_called()

    def test_stable_receipt_keeps_exact_byte_fingerprint_and_routing(self):
        for recognized, status in (('主缆不是竹篮', 'transcript_match'), ('竹篮不是竹篮', 'phonetic_review_required')):
            with self.subTest(status=status):
                self.report['recognized'] = recognized
                self.report['segments'] = [{'text': recognized}]; self.save()
                original = self.receipt.read_bytes()
                with patch('workflow.speech_gate._bounded_worker') as worker:
                    result = self.run_screen(); worker.assert_not_called()
                self.assertEqual(result['status'], status)
                self.assertEqual(result['receipt_sha256'], sha256(self.receipt))
                self.assertTrue(result['cache_hit']); self.assertFalse(result['content_approved'])
                self.assertEqual(self.receipt.read_bytes(), original)
