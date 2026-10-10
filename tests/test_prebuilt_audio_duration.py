"""Prebuilt voice/music must pass duration checks before output or receipt publication."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from workflow import runner as adapter
from workflow.plan import sha256


class PrebuiltAudioDurationTests(unittest.TestCase):
    def fixture(self,root,stage):
        audio=root/'supplied.wav';audio.write_bytes(b'supplied_audio')
        cached={'path':str(audio),'effective_narration':[{'text':'accepted narration'}],'segments':[{'text':'accepted segment'}]}
        plan={'input_sha256':{str(audio):sha256(audio)},'duration_seconds':30,
              ('prebuilt_narration' if stage=='voice' else 'prebuilt_music'):cached}
        task={'id':'fixture:'+stage,'token':'one','stage':stage,'deps':[],
              'payload':{'plan':plan,'folder':str(root),'data':{}}}
        folder=root/'attempts'/stage/'one';folder.mkdir(parents=True)
        output=folder/('narration.wav' if stage=='voice' else 'music.wav')
        output.write_bytes(b'previous_output');receipt=folder/'receipt.json';receipt.write_bytes(b'previous_receipt')
        queue=Mock();queue.results.return_value={}
        return queue,task,audio,output,receipt,cached

    def assert_unchanged(self,audio,output,receipt):
        self.assertEqual(audio.read_bytes(),b'supplied_audio')
        self.assertEqual(output.read_bytes(),b'previous_output')
        self.assertEqual(receipt.read_bytes(),b'previous_receipt')

    def test_nan_probe_cannot_publish_prebuilt_audio_or_success_receipt(self):
        for stage in ('voice','music'):
            for raw in ('NaN','nan'):
                with self.subTest(stage=stage,raw=raw),tempfile.TemporaryDirectory() as d:
                    queue,task,audio,output,receipt,cached=self.fixture(Path(d),stage)
                    with patch.object(adapter.media,'probe',return_value={'format':{'duration':raw}}) as probe,patch.object(adapter.shutil,'copyfile') as copy,patch.object(adapter.media,'narration') as narration,patch.object(adapter.media,'music') as music:
                        with self.assertRaisesRegex(adapter.StageError,'wrong duration') as error:adapter.execute(queue,task)
                        self.assertFalse(error.exception.retryable);self.assertFalse(error.exception.uncertain)
                        probe.assert_called_once_with(cached['path']);copy.assert_not_called();narration.assert_not_called();music.assert_not_called()
                    self.assert_unchanged(audio,output,receipt);self.assertEqual(queue.method_calls,[unittest.mock.call.results([])])

    def test_nonfinite_nonpositive_or_mismatched_duration_rejects_before_copy(self):
        for stage in ('voice','music'):
            for raw in ('Infinity','-Infinity','0','-1','29.8','30.2'):
                with self.subTest(stage=stage,raw=raw),tempfile.TemporaryDirectory() as d:
                    queue,task,audio,output,receipt,cached=self.fixture(Path(d),stage)
                    with patch.object(adapter.media,'probe',return_value={'format':{'duration':raw}}),patch.object(adapter.shutil,'copyfile') as copy:
                        with self.assertRaisesRegex(adapter.StageError,'wrong duration'):adapter.execute(queue,task)
                        copy.assert_not_called()
                    self.assert_unchanged(audio,output,receipt)

    def test_existing_tolerance_and_prebuilt_metadata_are_preserved(self):
        for stage in ('voice','music'):
            for raw in ('30','29.95','30.05'):
                with self.subTest(stage=stage,raw=raw),tempfile.TemporaryDirectory() as d:
                    queue,task,audio,output,receipt,cached=self.fixture(Path(d),stage)
                    with patch.object(adapter.media,'probe',return_value={'format':{'duration':raw}}) as probe,patch.object(adapter.media,'narration') as narration,patch.object(adapter.media,'music') as music:
                        result=adapter.execute(queue,task)
                        probe.assert_called_once_with(cached['path']);narration.assert_not_called();music.assert_not_called()
                    self.assertTrue(result['reused_audio']);self.assertEqual(output.read_bytes(),audio.read_bytes())
                    self.assertEqual(result['sha256'][str(output)],sha256(audio));self.assertEqual(json.loads(receipt.read_bytes()),result)
                    if stage=='voice':
                        self.assertEqual(result['effective_narration'],cached['effective_narration']);self.assertEqual(result['segments'],cached['segments'])

    def test_probe_failure_does_not_fallback_or_replace_previous_output(self):
        for stage in ('voice','music'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as d:
                queue,task,audio,output,receipt,cached=self.fixture(Path(d),stage)
                with patch.object(adapter.media,'probe',side_effect=RuntimeError('probe unavailable')),patch.object(adapter.shutil,'copyfile') as copy,patch.object(adapter.media,'narration') as narration,patch.object(adapter.media,'music') as music:
                    with self.assertRaisesRegex(RuntimeError,'probe unavailable'):adapter.execute(queue,task)
                    copy.assert_not_called();narration.assert_not_called();music.assert_not_called()
                self.assert_unchanged(audio,output,receipt)

    def test_changed_frozen_input_is_rejected_before_duration_probe(self):
        for stage in ('voice','music'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as d:
                queue,task,audio,output,receipt,cached=self.fixture(Path(d),stage);audio.write_bytes(b'changed_supplied_audio')
                with patch.object(adapter.media,'probe') as probe,patch.object(adapter.shutil,'copyfile') as copy:
                    with self.assertRaisesRegex(adapter.StageError,'input asset changed after enqueue'):adapter.execute(queue,task)
                    probe.assert_not_called();copy.assert_not_called()
                self.assertEqual(output.read_bytes(),b'previous_output');self.assertEqual(receipt.read_bytes(),b'previous_receipt')
                self.assertEqual(audio.read_bytes(),b'changed_supplied_audio')

if __name__=='__main__':unittest.main()
