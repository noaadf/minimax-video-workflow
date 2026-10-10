"""Revision preconditions and immutable-output conflicts are explicit failures."""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest

from workflow.core.io import sha256, write_json
from workflow.manifest_revision import OPS, RevisionSpec, derive, load_spec


class ManifestRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = json.loads(Path('workflow/examples/smoke_bridge.json').read_text())
        self.plan['id'] = 'example-whole-film-v1'
        self.sid = self.plan['shots'][0]['id']
        self.plan['shots'][0]['prompt'] = 'old camera old'
        self.plan['shots'][0]['reference_images'] = ['first.png', 'last.png']
        self.plan['shots'][0]['keyframe_prompts'] = {'start': 'start', 'end': 'end'}
        write_json(self.root / 'parent/manifest.json', self.plan)
        self.spec = RevisionSpec(self.root, 2, 'parent', 'whole-film-v1', 'whole-film-v2',
                                 'owner_directive', 'test diagnosis', 'test changes', ())

    def test_frozen_spec_load_and_receipt(self):
        path = self.root / 'spec.json'
        write_json(path, dict(self.spec.__dict__, product_folder=str(self.root), ops=[]))
        loaded = load_spec(path)
        self.assertEqual(loaded, self.spec)
        with self.assertRaises(FrozenInstanceError): loaded.attempt = 3
        receipt = derive(loaded)
        self.assertEqual(receipt['parent_manifest_sha256'], sha256(self.root / 'parent/manifest.json'))
        self.assertEqual(receipt['authority'], 'owner_directive')
        self.assertEqual(receipt['ops'], [])
        self.assertEqual(receipt['manifest_sha256'], sha256(self.root / 'manifest_story_rework_r2/manifest.json'))
        self.assertEqual(derive(loaded), receipt)

    def test_all_ops_apply_in_order_without_mutating_spec(self):
        source = json.loads(json.dumps(self.plan)); source['shots'][0]['camera'] = 'wide'
        write_json(self.root / 'copy/manifest.json', source)
        video = self.root / 'video.mp4'; video.write_bytes(b'video')
        ops = ({'op': 'prompt_edit', 'shot': self.sid, 'old': 'old', 'new': 'new', 'count': 2},
               {'op': 'drop', 'target': 'shots:*', 'keys': ['seed']},
               {'op': 'copy_from', 'shot': self.sid, 'manifest': 'copy', 'fields': ['camera']},
               {'op': 'reuse_video', 'shot': self.sid, 'path': str(video)},
               {'op': 'set', 'target': 'top', 'key': 'cover_title', 'value': '改版'},
               {'op': 'set', 'target': 'narration:0', 'key': 'preserve_delivery', 'value': True},
               {'op': 'drop', 'target': 'narration:0', 'keys': ['preserve_delivery']})
        spec = replace(self.spec, ops=ops)
        before = json.dumps(ops)
        receipt = derive(spec)
        manifest = json.loads((self.root / 'manifest_story_rework_r2/manifest.json').read_text())
        shot = manifest['shots'][0]
        self.assertEqual(shot['prompt'], 'new camera new')
        self.assertEqual(shot['camera'], 'wide')
        self.assertEqual(shot['source_path'], str(video))
        self.assertEqual(shot['previsual_path'], str(video))
        self.assertEqual(shot['source_start'], 0)
        self.assertNotIn('keyframe_prompts', shot)
        self.assertNotIn('reference_images', shot)
        self.assertNotIn('preserve_delivery', manifest['narration'][0])
        self.assertEqual(manifest['cover_title'], '改版')
        self.assertEqual(receipt['reused_videos'][self.sid]['sha256'], sha256(video))
        self.assertEqual(json.dumps(ops), before)

    def test_every_op_failure_path(self):
        cases = [
            {'op': 'prompt_edit', 'shot': self.sid, 'old': 'absent', 'new': 'new', 'count': 1},
            {'op': 'prompt_edit', 'shot': self.sid, 'old': 'old', 'new': 'new', 'count': 1},
            {'op': 'prompt_edit', 'shot': self.sid, 'old': '', 'new': 'new', 'count': 1},
            {'op': 'prompt_edit', 'shot': 'absent', 'old': 'old', 'new': 'new'},
            {'op': 'prompt_edit', 'shot': self.sid, 'old': 'old', 'new': 'new', 'count': True},
            {'op': 'drop', 'target': 'shot:absent', 'keys': ['seed']},
            {'op': 'drop', 'target': 'narration:999', 'keys': ['text']},
            {'op': 'drop', 'target': 'narration:-1', 'keys': ['text']},
            {'op': 'drop', 'target': 'unknown', 'keys': ['seed']},
            {'op': 'drop', 'target': 'top', 'keys': []},
            {'op': 'copy_from', 'shot': self.sid, 'manifest': 'missing', 'fields': ['prompt']},
            {'op': 'copy_from', 'shot': self.sid, 'manifest': '../escape', 'fields': ['prompt']},
            {'op': 'copy_from', 'shot': self.sid, 'manifest': 'parent', 'fields': ['missing']},
            {'op': 'copy_from', 'shot': 'absent', 'manifest': 'parent', 'fields': ['prompt']},
            {'op': 'reuse_video', 'shot': self.sid, 'path': str(self.root / 'missing.mp4')},
            {'op': 'reuse_video', 'shot': self.sid, 'path': 'relative.mp4'},
            {'op': 'set', 'target': 'unknown', 'key': 'cover_title', 'value': 'title'},
            {'op': 'set', 'target': 'top', 'key': '', 'value': 'title'},
            {'op': 'set', 'target': 'top', 'key': 'cover_title', 'value': ''},
            {'op': 'unknown'}, {'op': 'set'},
        ]
        self.assertEqual(set(OPS), {'prompt_edit', 'drop', 'copy_from', 'reuse_video', 'set'})
        for op in cases:
            with self.subTest(op=op), self.assertRaises(ValueError):
                derive(replace(self.spec, ops=(op,)))
        self.assertFalse((self.root / 'manifest_story_rework_r2').exists())

    def test_job_id_missing_or_multiple_fails_before_output(self):
        for identity in ('unrelated', 'whole-film-v1-whole-film-v1'):
            self.plan['id'] = identity
            write_json(self.root / 'parent/manifest.json', self.plan)
            with self.assertRaisesRegex(ValueError, 'exactly once'): derive(self.spec)
        self.assertFalse((self.root / 'manifest_story_rework_r2').exists())

    def test_manifest_and_receipt_conflicts(self):
        derive(self.spec)
        changed = replace(self.spec, ops=({'op': 'set', 'target': 'top', 'key': 'cover_title', 'value': '另一版'},))
        with self.assertRaisesRegex(ValueError, 'manifest conflicts'): derive(changed)
        with self.assertRaisesRegex(ValueError, 'receipt conflicts'):
            derive(replace(self.spec, authority='diagnosed_review'))
        dest = self.root / 'manifest_story_rework_r2/diagnosed_repair.json'
        dest.unlink()
        with self.assertRaisesRegex(ValueError, 'receipt conflicts'): derive(self.spec)

    def test_spec_and_output_preconditions(self):
        for kwargs in ({'attempt': 0}, {'attempt': True}, {'parent': '../parent'},
                       {'authority': 'unspecified'}, {'job_id_from': ''}, {'ops': []}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): replace(self.spec, **kwargs)
        with self.assertRaisesRegex(ValueError, 'Parent manifest missing'):
            derive(replace(self.spec, parent='missing'))
        dest = self.root / 'other'; dest.mkdir(); (dest / 'unrelated.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'nonempty'): derive(self.spec, output=dest)
        self.assertEqual((dest / 'unrelated.txt').read_text(), 'keep')
        derive(self.spec, output=self.root / 'explicit')
        self.assertFalse((self.root / 'manifest_story_rework_r2').exists())
