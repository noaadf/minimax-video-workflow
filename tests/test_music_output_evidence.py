"""Prepare output evidence before audio replacement, retaining partial publications."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import media as adapter
from workflow.core import io as plan_io
from workflow.plan import digest,sha256,write_json


class MusicOutputEvidenceTests(unittest.TestCase):
    def fixture(self,root,fresh=False,existing=True):
        source=root/'source.wav';source.write_bytes(b'source_audio')
        plan={'music':{'path':str(source)},'duration_seconds':30,'narration':[{'end':28}]}
        rid=digest({'recipe':4,'source_sha256':sha256(source),'music':plan['music'],'duration':30,'last_line_end':28})
        folder=root/'cache/music'/rid;folder.mkdir(parents=True)
        audio=folder/'music.wav';audio.write_bytes(b'accepted_audio')
        selection=audio.with_suffix('.selection.json');write_json(selection,{'offset_seconds':0,'span_seconds':30})
        receipt=folder/'receipt.json';write_json(receipt,{} if fresh else {'schema_version':2,'identity':rid,'sha256':sha256(audio),'selection_sha256':sha256(selection)})
        dest=root/'output.wav';evidence=dest.with_suffix('.selection.json')
        if existing:dest.write_bytes(b'previous_output');evidence.write_bytes(b'previous_evidence')
        return plan,dest,evidence,audio,receipt

    def render(self,plan,path):
        Path(path).write_bytes(b'accepted_audio');write_json(Path(path).with_suffix('.selection.json'),{'offset_seconds':0,'span_seconds':30})

    def assert_previous(self,dest,evidence,existing=True):
        if existing:
            self.assertEqual(dest.read_bytes(),b'previous_output');self.assertEqual(evidence.read_bytes(),b'previous_evidence')
        else:self.assertFalse(dest.exists());self.assertFalse(evidence.exists())

    def test_output_evidence_write_failure_preserves_pair_then_retries_cache(self):
        for fresh in (False,True):
            for existing in (False,True):
                with self.subTest(fresh=fresh,existing=existing),tempfile.TemporaryDirectory() as d:
                    root=Path(d);plan,dest,evidence,audio,receipt=self.fixture(root,fresh,existing)
                    def fail(path,value):
                        if Path(path).parent==root:
                            raise OSError('output evidence preparation interrupted')
                        return write_json(path,value)
                    with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                        with patch.object(plan_io,'write_json',side_effect=fail):
                            with self.assertRaisesRegex(OSError,'output evidence preparation interrupted'):adapter.music(plan,dest)
                        self.assert_previous(dest,evidence,existing)
                        self.assertEqual(list(root.glob('*.part*')),[])
                        self.assertEqual(render.call_count,int(fresh))
                        adapter.music(plan,dest);self.assertEqual(render.call_count,int(fresh))
                    self.assertEqual(dest.read_bytes(),b'accepted_audio')
                    self.assertEqual(json.loads(evidence.read_bytes())['audio_sha256'],sha256(dest))

    def test_evidence_staging_rename_failure_cannot_publish_audio(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,evidence,audio,receipt=self.fixture(root);replace=adapter.os.replace
            def fail(src,target):
                if Path(target).parent==root and 'selection.json' in Path(target).name:
                    raise OSError('evidence rename interrupted')
                return replace(src,target)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music') as render,patch.object(adapter.os,'replace',side_effect=fail):
                with self.assertRaisesRegex(OSError,'evidence rename interrupted'):adapter.music(plan,dest)
                render.assert_not_called()
            self.assert_previous(dest,evidence);self.assertEqual(list(root.glob('*.part*')),[])

    def test_prepared_snapshot_binds_audio_and_precedes_audio_publication(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,evidence,audio,receipt=self.fixture(root);replace=adapter.os.replace
            def inspect(src,target):
                if Path(target)==dest:
                    staged=list(root.glob('*.part.selection.json'));self.assertEqual(len(staged),1)
                    record=json.loads(staged[0].read_bytes());self.assertEqual(record['audio_sha256'],sha256(Path(src)))
                    self.assertEqual(record['cache_identity'],audio.parent.name);self.assert_previous(dest,evidence)
                return replace(src,target)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music') as render,patch.object(adapter.os,'replace',side_effect=inspect):
                adapter.music(plan,dest);render.assert_not_called()
            self.assertEqual(json.loads(evidence.read_bytes())['audio_sha256'],sha256(dest));self.assertEqual(list(root.glob('*.part*')),[])

    def test_final_evidence_failure_retains_bound_stage_and_retry_does_not_render(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,evidence,audio,receipt=self.fixture(root);replace=adapter.os.replace
            def fail(src,target):
                if Path(target)==evidence:raise OSError('final evidence publication interrupted')
                return replace(src,target)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music') as render:
                with patch.object(adapter.os,'replace',side_effect=fail):
                    with self.assertRaisesRegex(OSError,'final evidence publication interrupted'):adapter.music(plan,dest)
                staged=list(root.glob('*.part.selection.json'));self.assertEqual(len(staged),1)
                saved=staged[0].read_bytes();self.assertEqual(json.loads(saved)['audio_sha256'],sha256(dest))
                self.assertEqual(evidence.read_bytes(),b'previous_evidence')
                adapter.music(plan,dest);render.assert_not_called()
                self.assertEqual(staged[0].read_bytes(),saved)
            self.assertEqual(json.loads(evidence.read_bytes())['audio_sha256'],sha256(dest))

    def test_audio_publication_failure_preserves_previous_pair_and_cleans_prepared_copy(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,evidence,audio,receipt=self.fixture(root);replace=adapter.os.replace
            def fail(src,target):
                if Path(target)==dest:raise OSError('output audio publication interrupted')
                return replace(src,target)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music') as render,patch.object(adapter.os,'replace',side_effect=fail):
                with self.assertRaisesRegex(OSError,'output audio publication interrupted'):adapter.music(plan,dest)
                render.assert_not_called()
            self.assert_previous(dest,evidence);self.assertEqual(list(root.glob('*.part*')),[])

if __name__=='__main__':unittest.main()
