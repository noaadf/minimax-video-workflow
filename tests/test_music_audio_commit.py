"""Preserve verified staging and refuse replay before final music audio publication."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import media as adapter
from workflow.plan import digest, sha256, write_json


class MusicAudioCommitTests(unittest.TestCase):
    def fixture(self, root, existing=False):
        source=root/'source.wav';source.write_bytes(b'original_source')
        plan={'music':{'path':str(source)},'duration_seconds':30,'narration':[{'end':28}]}
        rid=digest({'recipe':4,'source_sha256':sha256(source),'music':plan['music'],'duration':30,'last_line_end':28})
        folder=root/'cache/music'/rid;folder.mkdir(parents=True)
        audio=folder/'music.wav';selection=audio.with_suffix('.selection.json')
        if existing:
            audio.write_bytes(b'old_audio');write_json(selection,{'offset_seconds':0,'span_seconds':30,'fixture_generation':'old'})
        receipt=folder/'receipt.json';receipt.write_bytes(b'{}')
        dest=root/'output.wav';dest.write_bytes(b'previous_output')
        dest.with_suffix('.selection.json').write_bytes(b'previous_evidence')
        return plan,dest,audio,selection,receipt,folder/'pending_receipt.json'

    def render(self, plan, path):
        Path(path).write_bytes(b'accepted_audio')
        write_json(Path(path).with_suffix('.selection.json'),{'offset_seconds':0,'span_seconds':30})

    def interrupt(self, plan, dest, audio):
        replace=adapter.os.replace
        def fail(src,target):
            if Path(target)==audio:raise OSError('audio publication interrupted')
            return replace(src,target)
        with patch.object(adapter.os,'replace',side_effect=fail):
            with self.assertRaisesRegex(OSError,'audio publication interrupted'):adapter.music(plan,dest)

    def stages(self, audio):
        stages=[p for p in audio.parent.glob('*.wav') if p!=audio]
        self.assertEqual(len(stages),1)
        return stages[0],stages[0].with_suffix('.selection.json')

    def test_failed_first_audio_commit_retains_bound_pair_and_blocks_replay(self):
        for existing in (False,True):
            with self.subTest(existing=existing),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root,existing)
                before=(audio.read_bytes(),selection.read_bytes()) if existing else None
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                    self.interrupt(plan,dest,audio)
                    staged_audio,staged_selection=self.stages(audio);record=json.loads(pending.read_bytes())
                    self.assertEqual(record.get('audio_staging'),staged_audio.name)
                    self.assertEqual(record['selection_staging'],staged_selection.name)
                    self.assertEqual(record['sha256'],sha256(staged_audio))
                    self.assertEqual(record['selection_sha256'],sha256(staged_selection))
                    intent=pending.read_bytes()
                    for _ in range(2):
                        with self.assertRaisesRegex(RuntimeError,'incomplete music cache audio commit'):adapter.music(plan,dest)
                    self.assertEqual(render.call_count,1);self.assertEqual(pending.read_bytes(),intent)
                if existing:self.assertEqual((audio.read_bytes(),selection.read_bytes()),before)
                else:self.assertFalse(audio.exists());self.assertFalse(selection.exists())
                self.assertEqual(dest.read_bytes(),b'previous_output')
                self.assertEqual(dest.with_suffix('.selection.json').read_bytes(),b'previous_evidence')
                self.assertEqual(receipt.read_bytes(),b'{}')
                self.assertTrue(staged_audio.exists());self.assertTrue(staged_selection.exists())

    def test_repeat_call_preserves_original_intent_without_second_render(self):
        for existing in (False,True):
            with self.subTest(existing=existing),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root,existing)
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                    self.interrupt(plan,dest,audio);intent=pending.read_bytes()
                    with self.assertRaisesRegex(RuntimeError,'incomplete music cache audio commit'):adapter.music(plan,dest)
                    self.assertEqual(render.call_count,1);self.assertEqual(pending.read_bytes(),intent)
                self.assertEqual(dest.read_bytes(),b'previous_output')

    def test_stage_mutation_or_untrusted_names_never_consumed(self):
        for mutation in ('changed','deleted','outside_path'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root)
                outside=root/'outside.wav';outside.write_bytes(b'outside_evidence')
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                    self.interrupt(plan,dest,audio);staged_audio,staged_selection=self.stages(audio)
                    if mutation=='changed':staged_audio.write_bytes(b'changed_audio')
                    elif mutation=='deleted':staged_audio.unlink();staged_selection.unlink()
                    else:
                        record=json.loads(pending.read_bytes())
                        record.update(audio_staging='../../outside.wav',selection_staging='../../outside.json')
                        write_json(pending,record)
                    intent=pending.read_bytes()
                    with self.assertRaisesRegex(RuntimeError,'incomplete music cache audio commit'):adapter.music(plan,dest)
                    self.assertEqual(render.call_count,1);self.assertEqual(pending.read_bytes(),intent)
                self.assertFalse(audio.exists());self.assertFalse(selection.exists())
                self.assertEqual(outside.read_bytes(),b'outside_evidence');self.assertEqual(dest.read_bytes(),b'previous_output')

    def test_previous_selection_stage_marker_also_prevents_audio_replay(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                self.interrupt(plan,dest,audio);record=json.loads(pending.read_bytes())
                record.pop('audio_staging',None);write_json(pending,record);intent=pending.read_bytes()
                with self.assertRaisesRegex(RuntimeError,'incomplete music cache audio commit'):adapter.music(plan,dest)
                self.assertEqual(render.call_count,1);self.assertEqual(pending.read_bytes(),intent)

    def test_explicit_verified_pair_restoration_recovers_without_render(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                self.interrupt(plan,dest,audio);staged_audio,staged_selection=self.stages(audio)
                record=json.loads(pending.read_bytes())
                self.assertEqual(record['sha256'],sha256(staged_audio))
                self.assertEqual(record['selection_sha256'],sha256(staged_selection))
                # Simulate explicit owner diagnosis in disposable data, with no overwrite.
                os.link(staged_audio,audio);os.link(staged_selection,selection)
                adapter.music(plan,dest);self.assertEqual(render.call_count,1)
            self.assertEqual(dest.read_bytes(),b'accepted_audio');self.assertFalse(pending.exists())
            self.assertEqual(json.loads(receipt.read_bytes())['sha256'],sha256(audio))
            self.assertEqual(json.loads(receipt.read_bytes())['selection_sha256'],sha256(selection))
            self.assertTrue(staged_audio.exists());self.assertTrue(staged_selection.exists())

if __name__=='__main__':unittest.main()
