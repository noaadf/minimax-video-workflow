"""No real transport: race final paths and interrupt a verified pair commit."""
import hashlib
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import cloud_speech
from workflow.plan import write_json


class TimedPublicationTests(unittest.TestCase):
    def exercise(self, folder, on_ready=None, fail_timing=False, corrupt_audio=False):
        audio=folder/'voice.mp3';words=folder/'words.json'
        payload=b'accepted_audio'
        raw=json.dumps({'script_text':'你好。','audio_sha256':hashlib.sha256(payload).hexdigest(),
            'boundaries':[{'offset':0,'duration':10000000,'text':'你好'}]}).encode()
        rid=cloud_speech.identity({'text':'你好。','voice':{},'boundary':'WordBoundary'})
        receipt={'request_id':rid,'path':f'/state/{rid}/speech.mp3','sha256':hashlib.sha256(payload).hexdigest(),
            'words_path':f'/state/{rid}/words.json','words_sha256':hashlib.sha256(raw).hexdigest()}
        cfg={'host':'example','port':22,'identity_file':'/key','code_root':'/runtime','state_root':'/state','python':'python'}
        def run(command,**kwargs):
            if command[0]=='scp' and command[-2].endswith('/speech.mp3'):Path(command[-1]).write_bytes(payload+b'corrupt' if corrupt_audio else payload)
            if command[0]=='scp' and command[-2].endswith('/words.json'):Path(command[-1]).write_bytes(raw)
            return types.SimpleNamespace(returncode=0,stdout=json.dumps(receipt).encode() if 'input' in kwargs else b'')
        def ready(path,value):
            write_json(path,value)
            if on_ready:on_ready(audio,words)
        original_link=cloud_speech.os.link;original_replace=cloud_speech.os.replace
        def publication(fn):
            def call(src,dst,*args,**kwargs):
                if fail_timing and Path(dst)==words:raise OSError('interrupted timing publication')
                return fn(src,dst,*args,**kwargs)
            return call
        with patch('workflow.cloud_speech.subprocess.run',side_effect=run),patch('workflow.media.duration',return_value=1.2),patch('workflow.core.io.write_json',side_effect=ready),patch('workflow.cloud_speech.os.link',side_effect=publication(original_link)),patch('workflow.cloud_speech.os.replace',side_effect=publication(original_replace)):
            return cloud_speech.synthesize_timed('你好。',{},audio,words,cfg)

    def assert_retained_pair(self,root):
        staged_audio=list(root.glob('*.part.mp3'));staged_words=list(root.glob('*.part.json'))
        self.assertEqual(len(staged_audio),1);self.assertEqual(len(staged_words),1)
        evidence=json.loads(staged_words[0].read_text())
        self.assertEqual(evidence['source_sha256'],hashlib.sha256(staged_audio[0].read_bytes()).hexdigest())
        self.assertEqual(evidence['source_path'],str(root/'voice.mp3'))

    def test_audio_created_during_transfer_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            with self.assertRaises(FileExistsError):self.exercise(root,lambda a,w:a.write_bytes(b'other_audio'))
            self.assertEqual((root/'voice.mp3').read_bytes(),b'other_audio')
            self.assertFalse((root/'words.json').exists());self.assert_retained_pair(root)

    def test_timing_created_during_transfer_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            with self.assertRaises(FileExistsError):self.exercise(root,lambda a,w:w.write_bytes(b'other_evidence'))
            self.assertEqual((root/'words.json').read_bytes(),b'other_evidence')
            self.assertEqual((root/'voice.mp3').read_bytes(),b'accepted_audio');self.assert_retained_pair(root)

    def test_interrupted_second_publication_retains_evidence_without_replay(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            with self.assertRaisesRegex(OSError,'interrupted timing'):self.exercise(root,fail_timing=True)
            self.assertEqual((root/'voice.mp3').read_bytes(),b'accepted_audio')
            self.assertFalse((root/'words.json').exists());self.assert_retained_pair(root)
            with patch('workflow.cloud_speech.subprocess.run') as remote:
                with self.assertRaisesRegex(ValueError,'new output paths'):
                    cloud_speech.synthesize_timed('你好。',{},root/'voice.mp3',root/'words.json',{'code_root':'/unused'})
                remote.assert_not_called()

    def test_success_binds_pair_and_cleans_only_staging(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();self.exercise(root)
            self.assertEqual((root/'voice.mp3').read_bytes(),b'accepted_audio')
            evidence=json.loads((root/'words.json').read_text())
            self.assertEqual(evidence['source_sha256'],cloud_speech.sha(root/'voice.mp3'))
            self.assertEqual(sorted(p.name for p in root.iterdir()),['voice.mp3','words.json'])

    def test_unverified_transfer_never_enters_pair_commit(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            with self.assertRaisesRegex(ValueError,'artifact hash differs'):
                self.exercise(root,corrupt_audio=True)
            self.assertEqual(list(root.iterdir()),[])

    def test_occupied_paths_are_rejected_before_transport(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();(root/'words.json').write_bytes(b'previous_evidence')
            with patch('workflow.cloud_speech.subprocess.run') as remote:
                with self.assertRaisesRegex(ValueError,'new output paths'):
                    cloud_speech.synthesize_timed('你好。',{},root/'voice.mp3',root/'words.json',{'code_root':'/unused'})
                remote.assert_not_called()
            self.assertEqual((root/'words.json').read_bytes(),b'previous_evidence')

if __name__=='__main__':unittest.main()
