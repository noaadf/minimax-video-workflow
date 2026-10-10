import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow.plan import write_json, sha256
from workflow.speech_gate import screen, retain_review


class SpeechGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.video = self.root/'video.mp4'; self.video.write_bytes(b'original mix')
        self.plan = {'id':'test', 'narration':[{'text':'主缆不是竹篮'}]}
        self.receipt = self.root/'receipt.json'
        self.report = {'binding':{'video_sha256':sha256(self.video), 'expected_text':'主缆不是竹篮',
            'settings':{'model':'test-model','model_files':{'model.bin':'abc'},'initial_prompt':None}},
            'expected':'主缆不是竹篮','recognized':'主缆不是竹篮','segments':[{'text':'主缆不是竹篮'}],
            'audio_source':'video','recognizer_prompted_with_expected_text':False,'report_path':str(self.receipt)}
        self.cfg = {'model_id':'test-model','model_files':{'model.bin':'abc'},
                    'receipt_indices':[str(self.root/'index.json')], 'cache_root':str(self.root/'cache'),
                    'allow_new_transcription':False}
        self.config = self.root/'config.json'; self.save()
    def save(self):
        write_json(self.receipt,self.report); write_json(self.root/'index.json',{'items':[self.report]})
        write_json(self.config,self.cfg)
    def run_screen(self):
        return screen(self.video,self.plan,self.root/'screen',self.config)
    def test_reuses_transcript_without_starting_model(self):
        with patch('workflow.speech_gate._bounded_worker',side_effect=AssertionError('must not run')):
            r=self.run_screen()
        self.assertTrue(r['cache_hit']); self.assertEqual(r['status'],'transcript_match')
        self.assertFalse(r['content_approved'])
    def test_changed_video_or_text_cannot_reuse_evidence(self):
        self.video.write_bytes(b'changed'); self.assertEqual(self.run_screen()['status'],'speech_not_assessed')
        self.video.write_bytes(b'original mix'); self.plan['narration'][0]['text']='主缆是竹篮'
        self.assertEqual(self.run_screen()['status'],'speech_not_assessed')
    def test_stem_prompted_or_different_model_receipt_is_rejected(self):
        for kind in ['stem','prompt','model','segments']:
            with self.subTest(kind=kind):
                old=json.loads(json.dumps(self.report))
                if kind=='stem':self.report['audio_source']='voice'
                if kind=='prompt':self.report['recognizer_prompted_with_expected_text']=True
                if kind=='model':self.report['binding']['settings']['model_files']={'model.bin':'changed'}
                if kind=='segments':self.report['segments']=[]
                self.save();self.assertEqual(self.run_screen()['status'],'speech_not_assessed')
                self.report=old
    def test_tone_uncertainty_retained_even_if_visual_passes(self):
        self.report['recognized']='竹篮不是竹篮';self.report['segments']=[{'text':self.report['recognized']}];self.save()
        r=self.run_screen();self.assertEqual(r['status'],'phonetic_review_required')
        visual={'status':'automatic_review_passed','review':{'verdict':'pass','findings':[]}}
        merged=retain_review(visual,r);self.assertEqual(merged['status'],'review_required')
        self.assertEqual(visual['status'],'automatic_review_passed')
        self.assertFalse(merged['review']['findings'][0]['confirmed_voice_error'])
    def test_homophones_do_not_clear_existing_visual_findings(self):
        self.report['recognized']='主览不是竹篮';self.report['segments']=[{'text':self.report['recognized']}];self.save()
        r=self.run_screen();self.assertEqual(r['status'],'homophone_spelling_only')
        visual={'status':'review_required','review':{'verdict':'review','findings':[{'category':'geometry'}]}}
        self.assertEqual(retain_review(visual,r),visual)
    def test_missing_runtime_is_review_not_technical_or_content_pass(self):
        self.config.unlink();r=self.run_screen()
        self.assertEqual(r['status'],'speech_not_assessed');self.assertTrue(r['review_required'])
    def test_failed_attempt_is_not_blindly_repeated(self):
        self.cfg.update(allow_new_transcription=True,receipt_indices=[],python=str(self.video),model_path=str(self.root))
        model=self.root/'model.bin';model.write_bytes(b'model');self.cfg['model_files']={'model.bin':sha256(model)};self.save()
        with patch('workflow.speech_gate._bounded_worker',side_effect=RuntimeError('failed')) as worker:
            self.assertEqual(self.run_screen()['status'],'speech_not_assessed')
            again=self.run_screen();self.assertIn('diagnose before retry',again['reason'])
            self.assertEqual(worker.call_count,1)
    def configure_worker(self):
        self.cfg.update(allow_new_transcription=True,receipt_indices=[],python=str(self.video),model_path=str(self.root))
        model=self.root/'model.bin';model.write_bytes(b'model');self.cfg['model_files']={'model.bin':sha256(model)};self.save()
    def test_new_worker_receipt_is_reused_on_next_call(self):
        self.configure_worker()
        def worker(command, log_path, timeout):
            dest=Path(command[command.index('--output')+1]); r=json.loads(json.dumps(self.report))
            r['binding']['settings']['model_files']=self.cfg['model_files']
            r['report_path']=str(dest/'raw.json');write_json(dest/'raw.json',r);write_json(dest/'index.json',{'items':[r]})
        with patch('workflow.speech_gate._bounded_worker',side_effect=worker) as process:
            first=self.run_screen();second=self.run_screen()
            self.assertEqual(first['status'],'transcript_match');self.assertFalse(first['cache_hit'])
            self.assertTrue(second['cache_hit']);self.assertEqual(process.call_count,1)
    def test_shutdown_failure_recovers_only_valid_bound_receipt(self):
        for valid in (True, False):
            with self.subTest(valid=valid):
                self.configure_worker()
                import shutil
                shutil.rmtree(self.root/'cache', ignore_errors=True)
                def worker(command, log_path, timeout):
                    dest=Path(command[command.index('--output')+1]); r=json.loads(json.dumps(self.report))
                    r['binding']['settings']['model_files']=self.cfg['model_files']
                    if not valid: r['binding']['video_sha256']='wrong'
                    r['report_path']=str(dest/'raw.json')
                    write_json(dest/'raw.json',r);write_json(dest/'index.json',{'items':[r]})
                    raise RuntimeError('ASR worker exited -6')
                with patch('workflow.speech_gate._bounded_worker',side_effect=worker):
                    result=self.run_screen()
                self.assertEqual(result['status'], 'transcript_match' if valid else 'speech_not_assessed')
                self.assertFalse(result['content_approved'])
                if valid: self.assertTrue(result['recovered_validated_receipt'])

    def test_busy_worker_does_not_spawn_duplicate(self):
        import fcntl
        self.configure_worker();cache=Path(self.cfg['cache_root']);cache.mkdir()
        with open(cache/'worker.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch('workflow.speech_gate._bounded_worker',side_effect=AssertionError('duplicate')):
                result=self.run_screen();self.assertIn('busy',result['reason'])
    def test_timeout_stops_worker(self):
        import sys
        from workflow.speech_gate import _bounded_worker
        with self.assertRaisesRegex(RuntimeError,'timed out'):
            _bounded_worker([sys.executable,'-c','import time; time.sleep(20)'],self.root/'worker.log',0.05)
