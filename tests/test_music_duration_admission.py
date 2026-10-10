"""Music cache admission with mocked render/probe; no media or model generation."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import media as adapter
from workflow.plan import write_json


class MusicDurationAdmissionTests(unittest.TestCase):
    def fixture(self,root):
        source=root/'source.wav';source.write_bytes(b'original_music')
        destination=root/'output.wav';destination.write_bytes(b'previous_music')
        destination.with_suffix('.selection.json').write_bytes(b'previous_selection')
        plan={'music':{'path':str(source)},'duration_seconds':30,'narration':[{'end':28}]}
        return plan,destination

    def render(self,plan,destination):
        Path(destination).write_bytes(b'rendered_music')
        write_json(Path(destination).with_suffix('.selection.json'),{'offset_seconds':0,'span_seconds':30})

    def test_nan_probe_cannot_replace_output_or_poison_cache(self):
        for value in ('NaN','nan'):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest=self.fixture(root)
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music',side_effect=self.render) as render,patch.object(adapter,'probe',return_value={'format':{'duration':value}}):
                    with self.assertRaisesRegex(ValueError,'wrong duration'):adapter.music(plan,dest)
                    self.assertEqual(render.call_count,1)
                self.assertEqual(dest.read_bytes(),b'previous_music')
                self.assertEqual(dest.with_suffix('.selection.json').read_bytes(),b'previous_selection')
                self.assertEqual(list((root/'cache').rglob('music.wav')),[])
                self.assertEqual(list((root/'cache').rglob('receipt.json')),[])
                self.assertEqual(list((root/'cache').rglob('*.selection.json')),[])

    def test_explicit_later_valid_call_does_not_reuse_invalid_render(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan,dest=self.fixture(root)
            with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music',side_effect=self.render) as render,patch.object(adapter,'probe',side_effect=[{'format':{'duration':'NaN'}},{'format':{'duration':'30'}}]):
                with self.assertRaisesRegex(ValueError,'wrong duration'):adapter.music(plan,dest)
                self.assertEqual(render.call_count,1)  # No internal retry.
                adapter.music(plan,dest)  # Separate explicit caller invocation, not recovery dispatch.
                self.assertEqual(render.call_count,2)
            selection=json.loads(dest.with_suffix('.selection.json').read_text())
            self.assertFalse(selection['cache_hit']);self.assertEqual(dest.read_bytes(),b'rendered_music')

    def test_invalid_or_mismatched_finite_duration_still_rejects(self):
        for value in ('Infinity','-Infinity','0','-1','29.8','30.2'):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest=self.fixture(root)
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music',side_effect=self.render),patch.object(adapter,'probe',return_value={'format':{'duration':value}}):
                    with self.assertRaisesRegex(ValueError,'wrong duration'):adapter.music(plan,dest)
                self.assertEqual(dest.read_bytes(),b'previous_music')
                self.assertEqual(list((root/'cache').rglob('receipt.json')),[])

    def test_existing_tolerance_and_verified_cache_reuse_are_preserved(self):
        for value in ('30','29.95','30.05'):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as d:
                root=Path(d);plan,dest=self.fixture(root)
                with patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':str(root/'cache')}),patch.object(adapter,'_render_music',side_effect=self.render) as render,patch.object(adapter,'probe',return_value={'format':{'duration':value}}) as probe:
                    adapter.music(plan,dest);adapter.music(plan,dest)
                    self.assertEqual(render.call_count,1);self.assertEqual(probe.call_count,1)
                self.assertEqual(dest.read_bytes(),b'rendered_music')
                self.assertTrue(json.loads(dest.with_suffix('.selection.json').read_text())['cache_hit'])

if __name__=='__main__':unittest.main()
