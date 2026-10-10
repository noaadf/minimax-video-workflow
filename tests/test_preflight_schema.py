"""T1 regression fixtures: local files only; provider transports are forbidden."""
import copy
from contextlib import closing
import importlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow.plan import sha256, write_json
from workflow.preflight_schema import (
    DEPENDENCIES, SCHEMAS, require_compiled_manifest, validate_manifest, validate_runtime,
)


def manifest():
    return {
        'schema_version': 1, 'id': 'fixture', 'title': '搬箱子', 'cover_title': '搬箱子',
        'template': 'science_reveal', 'duration_seconds': 15, 'aspect_ratio': '9:16',
        'continuity_bible': '同一房间，同一箱子', 'comment_prompt': '你会怎样搬？',
        'pacing_policy': 'storyboard_v1', 'caption_policy': 'speech_aligned_v1',
        'facts': [{'id': 'F1', 'text': '箱子被搬动', 'source_url': 'https://example.org/source'}],
        'music': {'path': 'fixture-music.wav', 'license_note': 'test fixture'},
        'story_contract': {
            'central_question': '怎样搬箱子', 'story_one_sentence': '工人搬动箱子',
            'protagonist': '工人', 'goal': '搬动', 'obstacle': '重量', 'resolution': '搬到桌上',
            'beats': [{'id': 'B1', 'caused_by': None, 'action': '搬起', 'result': '放下'}],
        },
        'shots': [{
            'id': 'S01', 'camera': 'wide', 'prompt': 'A worker lifts a plain box.',
            'duration_seconds': 15, 'duration_reason': '一个完整搬运动作', 'fact_ids': ['F1'],
            'production_mode': 'generated', 'storyboard': {
                'beat_id': 'B1', 'scene_id': 'room', 'purpose': '展示搬运',
                'attention_target': '箱子', 'action': '搬起', 'result': '放下',
                'cut_reason': '动作完成', 'state_before': {'box': 'floor'},
                'state_after': {'box': 'table'}, 'transition': {'kind': 'establish'},
            },
        }],
        'narration': [{
            'start': 0, 'end': 3, 'text': '工人搬起箱子。', 'fact_ids': ['F1'],
            'caption_chunks': ['工人搬起箱子。'], 'caption_lines': [['工人搬起箱子。']],
        }],
    }


class PreflightSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = manifest()
        self.plan['music']['path'] = str(self.file('music.wav'))
        # No test may reach a provider, even if an integration mock is omitted.
        for target in ('socket.create_connection', 'socket.socket.connect',
                       'urllib.request.urlopen', 'urllib.request.OpenerDirector.open'):
            guard = patch(target, side_effect=AssertionError('Network forbidden in T1 tests'))
            guard.start()
            self.addCleanup(guard.stop)

    def file(self, name, content=b'local fixture; not approved media'):
        path = self.root / name
        path.write_bytes(content)
        return path

    def save_plan(self):
        path = self.root / 'manifest.json'
        write_json(path, self.plan)
        return path

    def audio(self):
        return {
            'voice': {'audio_path': str(self.file('voice.wav')),
                      'effective_narration': copy.deepcopy(self.plan['narration'])},
            'bed': {'audio_path': self.plan['music']['path']},
        }

    def acceptance(self):
        paths = {'manifest': self.save_plan(), 'edl': self.root / 'edl.json'}
        write_json(paths['edl'], {'narration_path': str(self.file('voice.wav')),
                                 'music_path': self.plan['music']['path']})
        for name in ('visual_review', 'preview', 'review', 'contact', 'report'):
            paths[name] = self.file(name + '.json')
        value = {'status': 'ready_for_generation'}
        for name, path in paths.items():
            value.update({name + '_path': str(path), name + '_sha256': sha256(path)})
        path = self.root / 'acceptance.json'
        write_json(path, value)
        return path, value

    def compiler_fixture(self):
        from scripts import compile_batch_story_manifests as compiler
        folder = self.root / 'product'
        folder.mkdir()
        draft = {'ending': {'comment_prompt': self.plan.get('comment_prompt')},
                 'shots': copy.deepcopy(self.plan['shots'])}
        write_json(folder / 'draft.json', draft)
        write_json(folder / 'review.json', {'draft_sha256': sha256(folder / 'draft.json'),
                                          'review': {'verdict': 'pass'}})
        timeline = folder / 'whole_film_timeline.json'
        write_json(timeline, {
            'draft_sha256': sha256(folder / 'draft.json'),
            'plan_fragment': {key: self.plan[key] for key in (
                'duration_seconds', 'narration', 'caption_policy')},
            'semantic_cut_candidates': [{'duration_seconds': 15}],
        })
        write_json(folder / 'whole_film_voice/words.json', {
            'source_path': self.plan['music']['path'],
            'source_sha256': sha256(self.plan['music']['path']), 'voice': {},
        })
        base = self.root / 'base.json'; write_json(base, self.plan)
        packet = self.root / 'input.json'; write_json(packet, {
            'facts': self.plan['facts'], 'source_packet': {'fixture': True},
        })
        def route(*values):
            path = Path(*values)
            if str(path).startswith('output/production/batch_plans/'):
                return base
            if str(path).startswith('output/production/user_repair30_20261002/narrative/'):
                return packet
            return path
        answer = {key: copy.deepcopy(self.plan[key]) for key in (
            'shots', 'story_contract', 'continuity_bible')}
        return compiler, folder, timeline, route, answer

    def test_normal_compiled_manifest_all_eight_stages(self):
        self.assertEqual(set(SCHEMAS), {
            'narrative_review', 'image', 'video', 'speech', 'music', 'compose', 'quality', 'export'})
        for stage in (*SCHEMAS, 'compile'):
            with self.subTest(stage=stage):
                result = validate_manifest(self.plan, stage)
                self.assertTrue(result.ok, result.errors)
        self.assertEqual(require_compiled_manifest(self.plan), self.plan)

    def test_short_caption_can_use_existing_automatic_semantic_chunks(self):
        del self.plan['narration'][0]['caption_chunks']
        self.assertEqual(require_compiled_manifest(self.plan), self.plan)

    def test_missing_comment_prompt_and_all_other_missing_fields_are_reported(self):
        del self.plan['comment_prompt']; del self.plan['music']['license_note']
        del self.plan['shots'][0]['camera']; del self.plan['narration'][0]['text']
        result = validate_manifest(self.plan, 'compile')
        self.assertFalse(result.ok)
        for name in ('comment_prompt', 'music.license_note', 'shots[0].camera', 'narration[0].text'):
            self.assertTrue(any('manifest.' + name in error for error in result.errors))
        with self.assertRaisesRegex(ValueError, 'comment_prompt'):
            result.require()

    def test_pending_image_canvas_is_deferred_and_early_read_is_diagnosed(self):
        from workflow.storyboard_image_jobs import ImageJobs
        jobs = ImageJobs(self.root / 'images')
        jobs.add(self.save_plan(), 'fixture')
        task = json.loads(next(jobs.folder.glob('*.json')).read_text())
        self.assertNotIn('aspect_ratio', task)
        before = copy.deepcopy(task)
        result = validate_manifest(self.plan, 'image', timing='before_claim', task=task)
        self.assertTrue(result.ok, result.errors)
        self.assertIn('task.aspect_ratio: generated at claimed', result.deferred)
        result = validate_manifest(self.plan, 'image', timing='before_claim', task=task,
                                   required_reads=('task.aspect_ratio', 'task.claim_token'))
        self.assertEqual(len(result.errors), 2)
        self.assertTrue(all('timing violation' in error for error in result.errors))
        self.assertEqual(task, before)
        self.assertEqual(json.loads(next(jobs.folder.glob('*.json')).read_text()), before)
        claimed = jobs.claim(product='fixture')  # Local fixture only; no executor.
        self.assertEqual(claimed['aspect_ratio'], '9:16')
        self.assertTrue(validate_manifest(self.plan, 'image', timing='claimed', task=claimed).ok)

    def test_claimed_image_missing_canvas_reports_all_claim_fields(self):
        result = validate_manifest(self.plan, 'image', timing='claimed', task={})
        self.assertEqual(len(result.errors), 4)
        self.assertTrue(any('target_dimensions.width' in error for error in result.errors))

    def test_authored_canvas_is_checked_before_claim(self):
        self.plan['target_dimensions'] = {'width': 1920, 'height': 1080}
        self.assertTrue(any('manifest.canvas' in error for error in
                            validate_manifest(self.plan, 'image', timing='before_claim').errors))

    def test_audio_paths_are_required_only_at_their_declared_time(self):
        self.assertTrue(validate_manifest(self.plan, 'compose').ok)
        result = validate_manifest(self.plan, 'compose', timing='ready', context={
            'voice': {'effective_narration': self.plan['narration']}, 'bed': {}})
        self.assertEqual(len(result.errors), 2)
        self.assertTrue(any('voice.audio_path' in error for error in result.errors))
        self.assertTrue(any('bed.audio_path' in error for error in result.errors))
        self.assertTrue(validate_manifest(self.plan, 'compose', timing='ready', context=self.audio()).ok)
        for stage, path in (('speech', 'narration_path'), ('music', 'music_path')):
            with self.subTest(stage=stage):
                self.assertFalse(validate_manifest(self.plan, stage, timing='before_claim').ok)
                self.assertTrue(validate_manifest(self.plan, stage, timing='before_claim',
                                                  context={path: self.root / 'not_created_yet.wav'}).ok)

    def test_result_audio_files_must_exist_before_compose(self):
        audio = self.audio(); Path(audio['voice']['audio_path']).unlink()
        result = validate_manifest(self.plan, 'compose', timing='ready', context=audio)
        self.assertTrue(any('result file missing' in error for error in result.errors))

    def test_environment_aggregates_wrong_python_and_missing_broken_dependencies(self):
        original = importlib.import_module
        def imports(name):
            if name == 'opencc': raise ModuleNotFoundError('opencc absent')
            if name == 'pypinyin': raise RuntimeError('broken import')
            return original(name)
        del self.plan['comment_prompt']
        with patch('workflow.preflight_schema.sys.prefix', '/usr/local/system-python'), \
             patch('workflow.preflight_schema.importlib.import_module', side_effect=imports) as probe:
            result = validate_manifest(self.plan, 'compile')
        for name in ('python_prefix', 'dependencies.opencc', 'dependencies.pypinyin', 'comment_prompt'):
            self.assertTrue(any(name in error for error in result.errors))
        self.assertEqual([call.args[0] for call in probe.call_args_list], list(DEPENDENCIES))

    def test_current_workflow_environment_passes(self):
        result = validate_runtime()
        self.assertTrue(result.ok, result.errors)

    def test_unknown_stage_timing_and_bad_nested_values_return_diagnostics(self):
        self.assertFalse(validate_manifest(self.plan, 'unknown').ok)
        self.assertFalse(validate_manifest(self.plan, 'image', timing='unknown').ok)
        self.plan['shots'] = [None, {'duration_seconds': float('nan')}]
        self.plan['narration'][0]['start'] = False
        result = validate_manifest(self.plan, 'compile')
        self.assertTrue(any('shots[0].id' in error for error in result.errors))
        self.assertTrue(any('shots[1].duration_seconds' in error for error in result.errors))
        self.assertTrue(any('narration[0].start' in error for error in result.errors))

    def test_compiler_rejects_missing_comment_before_publishing_candidate(self):
        del self.plan['comment_prompt']; del self.plan['music']['license_note']
        compiler, folder, timeline, route, answer = self.compiler_fixture()
        with patch.object(compiler, 'Path', side_effect=route), \
             patch.object(compiler, 'ask_json', return_value=(answer, {'fixture': True})), \
             patch.object(compiler, 'bound_facts', return_value=self.plan['facts']):
            result = compiler.one(timeline)
        self.assertEqual(result['state'], 'isolated_adapter')
        self.assertIn('comment_prompt', result['detail'])
        self.assertIn('music.license_note', result['detail'])
        self.assertFalse((folder / 'manifest_candidate_v1/manifest.json').exists())

    def test_compiler_normal_path_publishes_valid_candidate(self):
        compiler, folder, timeline, route, answer = self.compiler_fixture()
        with patch.object(compiler, 'Path', side_effect=route), \
             patch.object(compiler, 'ask_json', return_value=(answer, {'fixture': True})), \
             patch.object(compiler, 'bound_facts', return_value=self.plan['facts']):
            result = compiler.one(timeline)
        self.assertEqual(result['state'], 'candidate_validated', result)
        self.assertTrue((folder / 'manifest_candidate_v1/manifest.json').is_file())

    def test_compiler_environment_failure_cannot_reach_llm_adapter(self):
        from scripts import compile_batch_story_manifests as compiler
        with patch('workflow.preflight_schema.sys.prefix', '/system'), \
             patch.object(compiler, 'ask_json') as api:
            result = compiler.one(self.root / 'timeline.json')
        self.assertEqual(result['state'], 'isolated_adapter')
        self.assertIn('environment.python_prefix', result['detail'])
        api.assert_not_called()

    def test_batch_missing_comment_stops_before_audio_or_review(self):
        from workflow import storyboard_batch as batch
        del self.plan['comment_prompt']
        with patch.object(batch.media, 'narration') as voice, \
             patch.object(batch.media, 'music') as bed, \
             patch.object(batch.storyboard, 'preflight') as review:
            result = batch.one({'product_id': 'fixture', 'manifest_path': str(self.save_plan())})
        self.assertEqual(result['state'], 'isolated_preflight')
        self.assertIn('comment_prompt', result['error'])
        voice.assert_not_called(); bed.assert_not_called(); review.assert_not_called()
        self.assertFalse((self.root / 'preflight').exists())

    def test_batch_binds_explicit_audio_paths_before_preflight(self):
        from workflow import storyboard_batch as batch
        def audio(plan, dest):
            Path(dest).write_bytes(b'fixture audio')
            return {'effective_narration': self.plan['narration']}
        with patch.object(batch.storyboard, 'missing_previsuals', return_value=[]), \
             patch.object(batch.media, 'narration', side_effect=audio), \
             patch.object(batch.media, 'music', side_effect=audio), \
             patch.object(batch.storyboard, 'preflight', return_value={'fixture': True}) as review:
            result = batch.one({'product_id': 'fixture', 'manifest_path': str(self.save_plan())})
        self.assertEqual(result['state'], 'ready_for_staging', result)
        plan, voice, bed, folder = review.call_args.args
        self.assertEqual(voice['audio_path'], str((folder / 'narration.wav').resolve()))
        self.assertEqual(bed['audio_path'], str((folder / 'music.wav').resolve()))

    def test_batch_missing_effective_audio_result_cannot_reach_review(self):
        from workflow import storyboard_batch as batch
        with patch.object(batch.storyboard, 'missing_previsuals', return_value=[]), \
             patch.object(batch.media, 'narration', return_value={}), \
             patch.object(batch.media, 'music'), \
             patch.object(batch.storyboard, 'preflight') as review:
            result = batch.one({'product_id': 'fixture', 'manifest_path': str(self.save_plan())})
        self.assertEqual(result['state'], 'isolated_preflight')
        self.assertIn('effective_narration', result['error'])
        review.assert_not_called()

    def test_existing_running_dispatch_does_not_retry_provider_work(self):
        from workflow import storyboard_batch as batch
        path = self.save_plan()
        prior = {'manifest_sha256': sha256(path), 'state': 'running', 'product_id': 'fixture'}
        write_json(self.root / 'preflight/dispatch.json', prior)
        with patch.object(batch.media, 'narration') as voice, \
             patch.object(batch.media, 'music') as bed, \
             patch.object(batch.storyboard, 'preflight') as review:
            self.assertEqual(batch.one({'product_id': 'fixture', 'manifest_path': str(path)}), prior)
        voice.assert_not_called(); bed.assert_not_called(); review.assert_not_called()

    def test_staging_checks_missing_manifest_fields_before_review_or_copy(self):
        from workflow import storyboard_batch as staging
        del self.plan['comment_prompt']
        acceptance, _ = self.acceptance()
        with patch.object(staging, 'require_review') as review, \
             patch.object(staging.shutil, 'copyfile') as copyfile:
            with self.assertRaisesRegex(ValueError, 'comment_prompt'):
                staging.stage(acceptance, 'fixture', 'parent', self.root)
        review.assert_not_called(); copyfile.assert_not_called()

    def test_staging_missing_edl_audio_paths_stops_before_copy(self):
        from workflow import storyboard_batch as staging
        acceptance, value = self.acceptance()
        write_json(value['edl_path'], {})
        value['edl_sha256'] = sha256(value['edl_path']); write_json(acceptance, value)
        with patch.object(staging.shutil, 'copyfile') as copyfile:
            with self.assertRaises(ValueError) as raised:
                staging.stage(acceptance, 'fixture', 'parent', self.root)
        self.assertIn('voice.audio_path', str(raised.exception))
        self.assertIn('bed.audio_path', str(raised.exception))
        copyfile.assert_not_called()

    def test_normal_staging_with_local_evidence(self):
        from workflow import storyboard_batch as staging
        self.plan['source_packet'] = {'fixture': 'verified elsewhere'}
        acceptance, _ = self.acceptance()
        with closing(sqlite3.connect(self.root / 'queue.sqlite')) as db, db:
            db.execute('CREATE TABLE jobs (id TEXT, manifest TEXT)')
            db.execute('INSERT INTO jobs VALUES (?,?)', ('parent', json.dumps(self.plan)))
        with patch.object(staging, 'require_review'), \
             patch.object(staging, 'bind_runtime_copy', side_effect=lambda a,b,c,d: Path(d).write_bytes(b'fixture review')), \
             patch.object(staging, 'reuse_preflight') as reuse, \
             patch('workflow.revised_sources.validate_revised_sources'):
            result = staging.stage(acceptance, 'fixture', 'parent', self.root)
        self.assertTrue(Path(result['manifest_path']).is_file())
        self.assertEqual(sha256(result['manifest_path']), result['manifest_sha256'])
        reuse.assert_called_once()

    def test_coordinator_blocks_invalid_manifest_before_production_adapter(self):
        from scripts import run_pipeline_coordinator as coordinator
        del self.plan['comment_prompt']
        spec = {'manifest_path': str(self.save_plan())}
        with patch.object(coordinator, 'production_step') as adapter:
            for phase in ('assets', 'downstream', 'delivery'):
                with self.subTest(phase=phase), self.assertRaisesRegex(ValueError, 'comment_prompt'):
                    coordinator.preflight_production_step(spec, phase, self.root)
        adapter.assert_not_called()

    def test_coordinator_preclaim_reads_manifest_without_pending_task_canvas(self):
        from scripts import run_pipeline_coordinator as coordinator
        (self.root / 'images').mkdir()
        write_json(self.root / 'images/task.json', {'state': 'pending'})
        spec = {'manifest_path': str(self.save_plan())}
        with patch.object(coordinator, 'production_step', return_value={'state': 'awaiting_images'}) as adapter:
            result = coordinator.preflight_production_step(spec, 'assets', self.root)
        self.assertEqual(result['state'], 'awaiting_images')
        adapter.assert_called_once_with(spec, 'assets', self.root)
        self.assertEqual(json.loads((self.root / 'images/task.json').read_text()), {'state': 'pending'})

    def test_coordinator_records_invalid_peer_without_blocking_valid_peer(self):
        from scripts import run_pipeline_coordinator as coordinator
        items = []
        for product in ('bad', 'good'):
            plan = copy.deepcopy(self.plan)
            if product == 'bad': del plan['comment_prompt']
            item = {'product_id': product, 'max_images': 0}
            for name, value in (('manifest', plan), ('draft', {}), ('narrative_review', {})):
                path = self.root / (product + '-' + name + '.json')
                write_json(path, value)
                item.update({name + '_path': str(path), name + '_sha256': sha256(path)})
            items.append(item)
        packet = self.root / 'packet.json'; write_json(packet, {'schema_version': 1, 'products': items})
        with patch.object(coordinator, 'production_step', return_value={'state': 'awaiting_images'}) as adapter:
            local = coordinator.Coordinator(self.root / 'coordinator.sqlite',
                                            adapter=coordinator.preflight_production_step)
            local.register(packet); local.tick(workers=1)
        rows = {row['product_id']: row for row in local.status()}
        self.assertEqual(rows['bad']['state'], 'isolated_error')
        self.assertIn('comment_prompt', rows['bad']['detail']['reason'])
        self.assertEqual(rows['good']['state'], 'awaiting_images')
        self.assertEqual(adapter.call_args.args[0]['product_id'], 'good')
        adapter.assert_called_once()

    def test_coordinator_wrong_environment_stops_before_opening_ledger(self):
        from scripts import run_pipeline_coordinator as coordinator
        with patch('sys.argv', ['coordinator', '--db', str(self.root / 'coordinator.sqlite')]), \
             patch('workflow.preflight_schema.sys.prefix', '/system'), \
             patch.object(coordinator, 'Coordinator') as ledger:
            with self.assertRaisesRegex(ValueError, 'python_prefix'):
                coordinator.main()
        ledger.assert_not_called()


if __name__ == '__main__':
    unittest.main()
