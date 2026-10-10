import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow.plan import sha256
from workflow.reference_repair import prepare_clip_recipe


class ClipRecipeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = json.loads(Path('workflow/examples/smoke_bridge.json').read_text())
        self.plan['id'] = 'parent'
        self.sid = self.plan['shots'][0]['id']
        self.video = self.root / 'reviewed.mp4'; self.video.write_bytes(b'exact-reviewed-bytes')
        self.candidate = {'video_path': str(self.video), 'sha256': {str(self.video): sha256(self.video)}}
        self.acceptance = {'accepted_for_' + self.sid + '_replacement': True,
            'video_sha256': sha256(self.video), 'allowed_parent_jobs': ['parent'],
            'requires_disclosure': '概念示意'}
        self.results = {s['id']: {'video_path': '/cached/' + s['id'] + '.mp4'} for s in self.plan['shots']}
        self.results.update(voice={'audio_path': '/cached/voice.wav', 'effective_narration': self.plan['narration'], 'segments': []},
                            music={'audio_path': '/cached/music.wav'})
        self.save()

    def save(self):
        (self.root / 'acceptance.json').write_text(json.dumps(self.acceptance))
        (self.root / 'result.json').write_text(json.dumps(self.candidate))
        (self.root / 'plan.json').write_text(json.dumps({'shots': [{'prompt': 'A reviewed, coherent horizontal bridge on one continuous axis.'}]}))

    def prepare(self, parent='parent'):
        with patch('workflow.reference_repair.job_bundle', return_value=(self.plan, self.results)):
            return prepare_clip_recipe(None, parent, {self.sid: self.root}, self.root / 'recipes')

    def test_recipe_preserves_script_caches_audio_and_mutes_native(self):
        recipe = self.prepare(); p = json.loads(Path(recipe['manifest_path']).read_text())
        self.assertEqual(p['facts'], self.plan['facts'])
        self.assertEqual(p['narration'], self.plan['narration'])
        self.assertFalse(p['shots'][0]['use_native_foley'])
        self.assertIn('prebuilt_music', p)
        self.assertIn('prebuilt_narration', p)
        self.assertEqual(self.prepare(), recipe)

    def test_review_does_not_transfer_to_another_parent(self):
        with self.assertRaisesRegex(ValueError, 'parent narration'):
            self.prepare('other-parent')

    def test_replaced_bytes_invalidate_acceptance(self):
        self.video.write_bytes(b'not-reviewed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.prepare()

    def test_legacy_unbound_acceptance_is_not_sufficient(self):
        self.acceptance.pop('video_sha256'); self.save()
        with self.assertRaisesRegex(ValueError, 'exact reviewed clip'):
            self.prepare()

    def test_rejected_probe_never_becomes_recipe(self):
        self.acceptance['accepted_for_' + self.sid + '_replacement'] = False; self.save()
        with self.assertRaisesRegex(ValueError, 'explicit acceptance'):
            self.prepare()


if __name__ == '__main__':
    unittest.main()
