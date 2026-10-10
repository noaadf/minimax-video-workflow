import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow.plan import sha256
from workflow.locked_audio_evidence import validate

class LockedAudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name)
        for name in ('source.mp4', 'candidate.mp4', 'voice.wav', 'music.wav'):
            (self.p / name).write_bytes(name.encode())
        self.plan = {'narration': [{'text': 'hello'}]}
        self.save('edl.json', {'narration_path': str(self.p/'voice.wav'), 'music_path': str(self.p/'music.wav'), 'caption_plan': self.plan})
        self.report = {'preview': str(self.p/'source.mp4'), 'preview_sha256': sha256(self.p/'source.mp4'), 'source_audio_in_mix': False, 'edl_sha256': sha256(self.p/'edl.json'), 'input_sha256': {str(self.p/n): sha256(self.p/n) for n in ('voice.wav','music.wav')}}
        self.save('report.json', self.report)
        self.save('mix.json', {'policy':'locked_approved_audio','source_audio_in_mix':False,'curated_effects':[],'audio_stream_copied':True,'source':{'path':str(self.p/'source.mp4'),'sha256':sha256(self.p/'source.mp4')}})
    def save(self, name, value):
        (self.p/name).write_text(json.dumps(value))
    def check(self, plan=None):
        return validate(self.p/'mix.json', self.p/'report.json', self.p/'candidate.mp4', plan or self.plan)
    @patch('workflow.locked_audio_evidence.decoded_hash', return_value='same')
    def test_valid_chain_does_not_imply_user_approval(self, mock):
        self.assertFalse(self.check()['user_approved'])
    def test_changed_stem_rejected(self):
        (self.p/'voice.wav').write_bytes(b'changed')
        with self.assertRaises(ValueError): self.check()
    def test_changed_script_rejected(self):
        with self.assertRaises(ValueError): self.check({'narration':[{'text':'new'}]})
    @patch('workflow.locked_audio_evidence.decoded_hash', side_effect=['a','b'])
    def test_changed_candidate_audio_rejected(self, mock):
        with self.assertRaises(ValueError): self.check()
    def test_native_audio_rejected(self):
        self.report['source_audio_in_mix'] = True
        self.save('report.json', self.report)
        with self.assertRaises(ValueError): self.check()
