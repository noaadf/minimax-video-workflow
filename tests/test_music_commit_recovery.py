"""Interrupt local music commits; recover only fully verified audio/selection pairs."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import media as adapter
from workflow.plan import digest, sha256, write_json


class MusicCommitRecoveryTests(unittest.TestCase):
    def fixture(self,root):
        source=root/'source.wav';source.write_bytes(b'original_source')
        plan={'music':{'path':str(source)},'duration_seconds':30,'narration':[{'end':28}]}
        rid=digest({'recipe':4,'source_sha256':sha256(source),'music':plan['music'],'duration':30,'last_line_end':28})
        folder=root/'cache/music'/rid;folder.mkdir(parents=True)
        audio=folder/'music.wav';audio.write_bytes(b'old_cache')
        selection=audio.with_suffix('.selection.json');write_json(selection,{'offset_seconds':0,'span_seconds':30})
        receipt=folder/'receipt.json';receipt.write_bytes(b'{}')
        dest=root/'output.wav';dest.write_bytes(b'previous_output')
        return plan,dest,audio,selection,receipt,folder/'pending_receipt.json'

    def render(self,plan,path):
        Path(path).write_bytes(b'accepted_render');write_json(Path(path).with_suffix('.selection.json'),{'offset_seconds':0,'span_seconds':30})

    def record(self,audio,selection,rid):
        return {'schema_version':2,'identity':rid,'sha256':sha256(audio),'selection_sha256':sha256(selection),'duration_seconds':30}

    def test_interrupted_receipt_commit_recovers_without_second_render(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root)
            replace=adapter.os.replace
            def fail(src,target):
                if Path(target)==receipt:raise OSError('receipt commit interrupted')
                return replace(src,target)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                with patch.object(adapter.os,'replace',side_effect=fail):
                    with self.assertRaisesRegex(OSError,'receipt commit interrupted'):adapter.music(plan,dest)
                self.assertEqual(dest.read_bytes(),b'previous_output');self.assertEqual(render.call_count,1)
                adapter.music(plan,dest);self.assertEqual(render.call_count,1)
            self.assertEqual(dest.read_bytes(),b'accepted_render')
            r=json.loads(receipt.read_bytes());self.assertEqual(r['sha256'],sha256(audio));self.assertEqual(r['selection_sha256'],sha256(selection))
            self.assertFalse(pending.exists());self.assertTrue(json.loads(dest.with_suffix('.selection.json').read_bytes())['cache_hit'])

    def test_journal_write_failure_preserves_old_pair_and_destination(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root)
            before=(audio.read_bytes(),selection.read_bytes(),receipt.read_bytes());replace=adapter.os.replace
            def fail(src,target):
                if Path(target)==pending:raise OSError('journal write interrupted')
                return replace(src,target)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render),patch.object(adapter.os,'replace',side_effect=fail):
                with self.assertRaisesRegex(OSError,'journal write interrupted'):adapter.music(plan,dest)
            self.assertEqual((audio.read_bytes(),selection.read_bytes(),receipt.read_bytes()),before)
            self.assertEqual(dest.read_bytes(),b'previous_output')

    def test_invalid_pending_identity_hash_or_duration_cannot_recover(self):
        edits=[lambda r:r.update(identity='other_request'),lambda r:r.update(sha256='wrong_audio'),
               lambda r:r.update(selection_sha256='wrong_selection'),lambda r:r.update(duration_seconds=float('nan')),
               lambda r:r.update(duration_seconds=True),lambda r:r.update(duration_seconds=29),
               lambda r:r.update(schema_version=1),lambda r:r.pop('selection_sha256')]
        for edit in edits:
            with self.subTest(edit=edit),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root)
                record=self.record(audio,selection,audio.parent.name);edit(record);write_json(pending,record)
                before=pending.read_bytes()
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music',side_effect=RuntimeError('explicit render unavailable')) as render:
                    with self.assertRaisesRegex(RuntimeError,'explicit render unavailable'):adapter.music(plan,dest)
                    self.assertEqual(render.call_count,1)
                self.assertEqual(pending.read_bytes(),before);self.assertEqual(receipt.read_bytes(),b'{}')
                self.assertEqual(dest.read_bytes(),b'previous_output')

    def test_valid_main_receipt_takes_precedence_over_stale_pending(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root)
            write_json(receipt,self.record(audio,selection,audio.parent.name));pending.write_bytes(b'uncertain_old_journal')
            before=(receipt.read_bytes(),pending.read_bytes())
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music') as render,patch.object(adapter,'duration') as probe:
                adapter.music(plan,dest);render.assert_not_called();probe.assert_not_called()
            self.assertEqual((receipt.read_bytes(),pending.read_bytes()),before)

    def test_recovery_promotion_failure_never_falls_through_to_render(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root)
            write_json(pending,self.record(audio,selection,audio.parent.name));replace=adapter.os.replace
            def fail(src,target):
                if Path(src)==pending and Path(target)==receipt:raise OSError('promotion interrupted')
                return replace(src,target)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render,patch.object(adapter.os,'replace',side_effect=fail):
                for _ in range(2):
                    with self.assertRaisesRegex(OSError,'promotion interrupted'):adapter.music(plan,dest)
                render.assert_not_called()
            self.assertTrue(pending.exists());self.assertEqual(dest.read_bytes(),b'previous_output')

    def test_normal_commit_consumes_journal_and_preserves_existing_cache_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest,audio,selection,receipt,pending=self.fixture(root)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'duration',return_value=30),patch.object(adapter,'_render_music',side_effect=self.render) as render:
                adapter.music(plan,dest);adapter.music(plan,dest);self.assertEqual(render.call_count,1)
            self.assertFalse(pending.exists());self.assertEqual(dest.read_bytes(),b'accepted_render')
            self.assertEqual(json.loads(receipt.read_bytes()).get('duration_seconds'),30)

if __name__=='__main__':unittest.main()
