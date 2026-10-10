"""Inject partial copies and cache mutations without rendering real media."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import media as adapter
from workflow.plan import digest, sha256, write_json


class MusicOutputTransferTests(unittest.TestCase):
    def fixture(self,root,fresh=False):
        source=root/'source.wav';source.write_bytes(b'original_source')
        plan={'music':{'path':str(source)},'duration_seconds':30,'narration':[{'end':28}]}
        rid=digest({'recipe':4,'source_sha256':sha256(source),'music':plan['music'],'duration':30,'last_line_end':28})
        folder=root/'cache/music'/rid;folder.mkdir(parents=True)
        audio=folder/'music.wav';audio.write_bytes(b'accepted_music')
        selection=audio.with_suffix('.selection.json');write_json(selection,{'offset_seconds':0,'span_seconds':30})
        receipt=folder/'receipt.json';write_json(receipt,{} if fresh else {'schema_version':2,'identity':rid,'sha256':sha256(audio),'selection_sha256':sha256(selection)})
        dest=root/'output.wav';dest.write_bytes(b'previous_music');dest.with_suffix('.selection.json').write_bytes(b'previous_selection')
        return plan,dest,audio,receipt

    def render(self,plan,path):
        Path(path).write_bytes(b'accepted_music');write_json(Path(path).with_suffix('.selection.json'),{'offset_seconds':0,'span_seconds':30})

    def assert_original(self,root,dest):
        self.assertEqual(dest.read_bytes(),b'previous_music')
        self.assertEqual(dest.with_suffix('.selection.json').read_bytes(),b'previous_selection')
        self.assertEqual(list(root.glob('*.part')),[])

    def test_interrupted_copy_preserves_output_without_rerender(self):
        for fresh in (False,True):
            with self.subTest(fresh=fresh),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,receipt=self.fixture(root,fresh)
                def partial(src,target):
                    Path(target).write_bytes(b'partial');raise OSError('copy interrupted')
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render,patch('shutil.copyfile',side_effect=partial) as copies:
                    with self.assertRaisesRegex(OSError,'copy interrupted'):adapter.music(plan,dest)
                    self.assertEqual(copies.call_count,1);self.assertEqual(render.call_count,int(fresh))
                self.assert_original(root,dest)
                self.assertEqual(json.loads(receipt.read_bytes())['sha256'],sha256(audio))

    def test_wrong_copied_bytes_cannot_replace_output(self):
        for fresh in (False,True):
            with self.subTest(fresh=fresh),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,receipt=self.fixture(root,fresh)
                def corrupt(src,target):Path(target).write_bytes(b'wrong_copy')
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render,patch('shutil.copyfile',side_effect=corrupt) as copies:
                    with self.assertRaisesRegex(ValueError,'music output differs'):adapter.music(plan,dest)
                    self.assertEqual(copies.call_count,1);self.assertEqual(render.call_count,int(fresh))
                self.assert_original(root,dest)

    def test_cache_mutation_after_admission_is_not_rebound_to_output(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,receipt=self.fixture(root);copy=shutil.copyfile
            def change(src,target):
                audio.write_bytes(b'changed_after_screening');return copy(src,target)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music') as render,patch('shutil.copyfile',side_effect=change):
                with self.assertRaisesRegex(ValueError,'music output differs'):adapter.music(plan,dest)
                render.assert_not_called()
            self.assert_original(root,dest)
            self.assertNotEqual(json.loads(receipt.read_bytes())['sha256'],sha256(audio))

    def test_fresh_publication_keeps_the_validated_audio_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,receipt=self.fixture(root,True);replace=adapter.os.replace
            def change(src,target):
                result=replace(src,target)
                if Path(target)==audio:audio.write_bytes(b'changed_after_render_validation')
                return result
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render,patch.object(adapter.os,'replace',side_effect=change):
                with self.assertRaisesRegex(ValueError,'music output differs'):adapter.music(plan,dest)
                self.assertEqual(render.call_count,1)
            self.assert_original(root,dest)
            expected=root/'expected.wav';expected.write_bytes(b'accepted_music')
            self.assertEqual(json.loads(receipt.read_bytes())['sha256'],sha256(expected))
            self.assertNotEqual(json.loads(receipt.read_bytes())['sha256'],sha256(audio))

    def test_success_publishes_verified_new_or_existing_output(self):
        for existing in (False,True):
            with self.subTest(existing=existing),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,receipt=self.fixture(root)
                if not existing:dest.unlink();dest.with_suffix('.selection.json').unlink()
                copy=shutil.copyfile
                def in_flight(src,target):
                    Path(target).write_bytes(b'partial_copy')
                    if existing:self.assertEqual(dest.read_bytes(),b'previous_music')
                    else:self.assertFalse(dest.exists())
                    return copy(src,target)
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music') as render,patch('shutil.copyfile',side_effect=in_flight):
                    adapter.music(plan,str(dest));render.assert_not_called()
                self.assertEqual(dest.read_bytes(),b'accepted_music')
                self.assertTrue(json.loads(dest.with_suffix('.selection.json').read_bytes())['cache_hit'])
                self.assertEqual(list(root.glob('*.part')),[])

if __name__=='__main__':unittest.main()
