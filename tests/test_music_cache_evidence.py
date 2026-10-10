"""Bind music cache identity and exact selection bytes; no real rendering."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import media as adapter
from workflow.plan import digest, sha256, write_json


class MusicEvidenceTests(unittest.TestCase):
    def fixture(self,root):
        source=root/'source.wav';source.write_bytes(b'original_source')
        plan={'music':{'path':str(source)},'duration_seconds':30,'narration':[{'end':28}]}
        rid=digest({'recipe':4,'source_sha256':sha256(source),'music':plan['music'],'duration':30,'last_line_end':28})
        folder=root/'cache/music'/rid;folder.mkdir(parents=True)
        audio=folder/'music.wav';audio.write_bytes(b'cached_audio')
        selection=audio.with_suffix('.selection.json');selection.write_bytes(b'{"offset_seconds":0,"span_seconds":30}')
        receipt=folder/'receipt.json';record={'identity':rid,'sha256':sha256(audio),'selection_sha256':sha256(selection)};write_json(receipt,record)
        dest=root/'output.wav';dest.write_bytes(b'previous_output');dest.with_suffix('.selection.json').write_bytes(b'previous_evidence')
        return plan,dest,audio,selection,receipt,record

    def render(self,plan,dest):
        Path(dest).write_bytes(b'new_valid_audio')
        write_json(Path(dest).with_suffix('.selection.json'),{'offset_seconds':0,'span_seconds':30})

    def test_other_request_receipt_is_not_a_cache_hit(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,record=self.fixture(root)
            write_json(receipt,{**record,'identity':'other_request'})
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                adapter.music(plan,dest);self.assertEqual(render.call_count,1)
            self.assertEqual(dest.read_bytes(),b'new_valid_audio')
            self.assertFalse(json.loads(dest.with_suffix('.selection.json').read_bytes())['cache_hit'])

    def test_changed_bound_selection_is_not_blessed_by_audio_sha(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,record=self.fixture(root)
            selection.write_bytes(b'{"offset_seconds":99}')
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                adapter.music(plan,dest);self.assertEqual(render.call_count,1)
            self.assertEqual(json.loads(dest.with_suffix('.selection.json').read_bytes())['offset_seconds'],0)

    def test_malformed_receipt_is_a_miss_with_one_local_render(self):
        for raw in (b'null',b'[]',b'"wrong root"',b'{'):
            with self.subTest(raw=raw),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,selection,receipt,record=self.fixture(root);receipt.write_bytes(raw)
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                    adapter.music(plan,dest);self.assertEqual(render.call_count,1)
                self.assertEqual(dest.read_bytes(),b'new_valid_audio')

    def test_bad_cached_selection_cannot_overwrite_destination_before_diagnosis(self):
        for raw in (b'null',b'[]',b'"wrong root"',b'{',b'\xff'):
            with self.subTest(raw=raw),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,selection,receipt,record=self.fixture(root)
                selection.write_bytes(raw);write_json(receipt,{**record,'selection_sha256':sha256(selection)})
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music',side_effect=RuntimeError('local renderer unavailable')) as render:
                    with self.assertRaisesRegex(RuntimeError,'local renderer unavailable'):adapter.music(plan,dest)
                    self.assertEqual(render.call_count,1)
                self.assertEqual(dest.read_bytes(),b'previous_output')
                self.assertEqual(dest.with_suffix('.selection.json').read_bytes(),b'previous_evidence')
                self.assertEqual(audio.read_bytes(),b'cached_audio')

    def test_fresh_invalid_selection_rejects_before_cache_or_output_publication(self):
        for raw in (b'null',b'[]',b'"wrong root"',b'{'):
            with self.subTest(raw=raw),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,selection,receipt,record=self.fixture(root)
                receipt.write_bytes(b'{}')  # Force the preexisting cache miss branch.
                def bad_render(plan,path):
                    Path(path).write_bytes(b'new_invalid_audio');Path(path).with_suffix('.selection.json').write_bytes(raw)
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=bad_render):
                    with self.assertRaises(ValueError):adapter.music(plan,dest)
                self.assertEqual(dest.read_bytes(),b'previous_output');self.assertEqual(audio.read_bytes(),b'cached_audio')
                self.assertEqual(selection.read_bytes(),b'{"offset_seconds":0,"span_seconds":30}')
                self.assertEqual(receipt.read_bytes(),b'{}')

    def test_new_receipt_binds_exact_selection_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,record=self.fixture(root);receipt.write_bytes(b'{}')
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render):adapter.music(plan,dest)
            self.assertEqual(json.loads(receipt.read_bytes()).get('selection_sha256'),sha256(selection))

    def test_versioned_receipt_requires_binding_and_known_schema(self):
        for version in (2,99):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,selection,receipt,record=self.fixture(root)
                record['schema_version']=version
                if version==2:record.pop('selection_sha256')
                write_json(receipt,record)
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                    adapter.music(plan,dest);self.assertEqual(render.call_count,1)
                self.assertFalse(json.loads(dest.with_suffix('.selection.json').read_bytes())['cache_hit'])

    def test_valid_legacy_receipt_reuses_without_rewriting_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,record=self.fixture(root);record.pop('selection_sha256');write_json(receipt,record)
            before=(receipt.read_bytes(),selection.read_bytes())
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music') as render,patch.object(adapter,'duration') as probe:
                adapter.music(plan,dest);render.assert_not_called();probe.assert_not_called()
            self.assertEqual(dest.read_bytes(),b'cached_audio');self.assertEqual((receipt.read_bytes(),selection.read_bytes()),before)
            self.assertTrue(json.loads(dest.with_suffix('.selection.json').read_bytes())['cache_hit'])

    def test_output_uses_validated_selection_snapshot_after_copy(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,record=self.fixture(root)
            copy=shutil.copyfile
            def replace_after_copy(src,dst):
                result=copy(src,dst)
                if Path(src)==audio:selection.write_bytes(b'{"offset_seconds":99}')
                return result
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch('shutil.copyfile',side_effect=replace_after_copy),patch.object(adapter,'_render_music') as render:
                adapter.music(plan,dest);render.assert_not_called()
            self.assertEqual(json.loads(dest.with_suffix('.selection.json').read_bytes())['offset_seconds'],0)
            self.assertEqual(json.loads(selection.read_bytes())['offset_seconds'],99)

if __name__=='__main__':unittest.main()
