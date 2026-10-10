"""Retain selection evidence after audio commit and refuse implicit renderer replay."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import media as adapter
from workflow.plan import digest, sha256, write_json


class MusicPartialCommitTests(unittest.TestCase):
    def fixture(self,root,existing=True):
        source=root/'source.wav';source.write_bytes(b'original_source')
        plan={'music':{'path':str(source)},'duration_seconds':30,'narration':[{'end':28}]}
        rid=digest({'recipe':4,'source_sha256':sha256(source),'music':plan['music'],'duration':30,'last_line_end':28})
        folder=root/'cache/music'/rid;folder.mkdir(parents=True)
        audio=folder/'music.wav';selection=audio.with_suffix('.selection.json')
        if existing:audio.write_bytes(b'old_audio');write_json(selection,{'offset_seconds':0,'span_seconds':30,'fixture_generation':'old'})
        receipt=folder/'receipt.json';receipt.write_bytes(b'{}')
        dest=root/'output.wav';dest.write_bytes(b'previous_output');dest.with_suffix('.selection.json').write_bytes(b'previous_evidence')
        return plan,dest,audio,selection,receipt,folder/'pending_receipt.json'

    def render(self,plan,path):
        Path(path).write_bytes(b'accepted_audio');write_json(Path(path).with_suffix('.selection.json'),{'offset_seconds':0,'span_seconds':30})

    def interrupt(self,plan,dest,selection):
        replace=adapter.os.replace
        def fail(src,target):
            if Path(target)==selection:raise OSError('selection publication interrupted')
            return replace(src,target)
        with patch.object(adapter.os,'replace',side_effect=fail):
            with self.assertRaisesRegex(OSError,'selection publication interrupted'):adapter.music(plan,dest)

    def test_selection_failure_retains_bound_staging_and_blocks_replay(self):
        for existing in (False,True):
            with self.subTest(existing=existing),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root,existing)
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                    self.interrupt(plan,dest,selection)
                    staged=list(audio.parent.glob('*.selection.json'));staged=[p for p in staged if p!=selection]
                    self.assertEqual(len(staged),1)
                    record=json.loads(pending.read_bytes());self.assertEqual(record['selection_staging'],staged[0].name)
                    self.assertEqual(record['selection_sha256'],sha256(staged[0]));self.assertEqual(record['sha256'],sha256(audio))
                    for _ in range(2):
                        with self.assertRaisesRegex(RuntimeError,'incomplete music cache selection commit'):adapter.music(plan,dest)
                    self.assertEqual(render.call_count,1)
                self.assertEqual(dest.read_bytes(),b'previous_output');self.assertEqual(dest.with_suffix('.selection.json').read_bytes(),b'previous_evidence')
                self.assertEqual(receipt.read_bytes(),b'{}');self.assertTrue(staged[0].exists())
                if existing:self.assertEqual(json.loads(selection.read_bytes())['offset_seconds'],0)

    def test_second_call_after_partial_publication_does_not_render_again(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                self.interrupt(plan,dest,selection)
                with self.assertRaisesRegex(RuntimeError,'incomplete music cache selection commit'):adapter.music(plan,dest)
                self.assertEqual(render.call_count,1)
            self.assertEqual(dest.read_bytes(),b'previous_output')

    def test_changed_or_deleted_stage_still_requires_diagnosis(self):
        for mutation in ('changed','deleted','outside_path'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root)
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                    self.interrupt(plan,dest,selection);record=json.loads(pending.read_bytes())
                    stage=audio.parent/record['selection_staging']
                    if mutation=='changed':stage.write_bytes(b'changed_stage')
                    elif mutation=='deleted':stage.unlink()
                    else:record['selection_staging']='../../outside.json';write_json(pending,record)
                    before=pending.read_bytes()
                    with self.assertRaisesRegex(RuntimeError,'incomplete music cache selection commit'):adapter.music(plan,dest)
                    self.assertEqual(render.call_count,1);self.assertEqual(pending.read_bytes(),before)
                self.assertEqual(dest.read_bytes(),b'previous_output')

    def test_current_malformed_selection_does_not_hide_partial_commit(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                self.interrupt(plan,dest,selection);selection.write_bytes(b'null')
                with self.assertRaisesRegex(RuntimeError,'incomplete music cache selection commit'):adapter.music(plan,dest)
                self.assertEqual(render.call_count,1)
            self.assertEqual(selection.read_bytes(),b'null');self.assertEqual(dest.read_bytes(),b'previous_output')

    def test_after_verified_selection_is_restored_receipt_recovers_without_render(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root,False)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                self.interrupt(plan,dest,selection);record=json.loads(pending.read_bytes());stage=audio.parent/record['selection_staging']
                self.assertEqual(sha256(stage),record['selection_sha256'])
                os.link(stage,selection)  # Simulated explicit owner diagnosis; never automatic in implementation.
                adapter.music(plan,dest);self.assertEqual(render.call_count,1)
            self.assertEqual(dest.read_bytes(),b'accepted_audio');self.assertFalse(pending.exists())
            self.assertEqual(json.loads(receipt.read_bytes())['selection_sha256'],sha256(selection));self.assertTrue(stage.exists())

if __name__=='__main__':unittest.main()
