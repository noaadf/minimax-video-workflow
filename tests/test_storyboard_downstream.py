import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from workflow import storyboard_downstream as runner
from workflow.plan import sha256, write_json
from workflow.storyboard_image_jobs import ImageJobs


class DownstreamTests(unittest.TestCase):
    def fixture(self, root):
        manifest = root / 'manifest.json'
        draft = root / 'draft.json'
        write_json(manifest, {'shots': [{'id': 'S01', 'prompt': 'one box is moved',
                   'storyboard': {'state_before': {'x': 0}, 'state_after': {'x': 1}}}]})
        write_json(draft, {'draft': True})
        jobs = ImageJobs(root / 'images')
        jobs.add(manifest, 'product')
        item = {'product_id': 'product', 'manifest_path': str(manifest), 'manifest_sha256': sha256(manifest),
                'draft_path': str(draft), 'draft_sha256': sha256(draft), 'image_jobs_dir': str(jobs.folder)}
        return item, jobs

    def complete(self, root, jobs):
        task = jobs.claim('product')
        image = root / (task['role'] + '.png')
        Image.new('RGB', (36, 64), 'red' if task['role'] == 'start' else 'blue').save(image)
        return jobs.complete(task['id'], task['claim_token'], image)

    def test_requires_entire_expected_role_set_even_when_job_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); item, jobs = self.fixture(root)
            self.complete(root, jobs)
            end = next(p for p in jobs.folder.glob('*.json') if json.loads(p.read_text())['role'] == 'end')
            end.unlink()
            with self.assertRaises(runner.Paused) as raised:
                runner.complete_assets(item)
            self.assertEqual(raised.exception.state, 'awaiting_images')
            self.assertEqual(raised.exception.detail['pending'][0]['state'], 'missing_job')

    def test_real_completed_images_are_checked_and_ordered_start_then_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); item, jobs = self.fixture(root)
            self.complete(root, jobs); self.complete(root, jobs)
            assets = runner.complete_assets(item)
            self.assertEqual([a['role'] for a in assets], ['start', 'end'])
            Path(assets[0]['path']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Changed'):
                runner.complete_assets(item)

    def test_changed_manifest_and_rejected_canvas_cannot_reach_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); item, jobs = self.fixture(root)
            self.complete(root, jobs); self.complete(root, jobs)
            path = next(jobs.folder.glob('*.json')); task = json.loads(path.read_text())
            task['canvas_check']['status'] = 'failed'; write_json(path, task)
            with self.assertRaisesRegex(ValueError, 'canvas'):
                runner.complete_assets(item)
            Path(item['manifest_path']).write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Changed'):
                runner.complete_assets(item)

    def test_interrupted_external_call_is_not_blindly_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); call = Mock()
            write_json(root / 'review.checkpoint.json', {'state': 'running', 'input_digest': 'sha'})
            with self.assertRaises(runner.Paused):
                runner.checkpoint(root, 'review', 'sha', call, Mock(side_effect=ValueError('no receipt')))
            call.assert_not_called()

    def test_completed_evidence_recovers_interrupted_checkpoint_without_new_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); call = Mock()
            write_json(root / 'review.checkpoint.json', {'state': 'running', 'input_digest': 'sha'})
            result = runner.checkpoint(root, 'review', 'sha', call, lambda: {'verified': True})
            self.assertTrue(result['verified']); call.assert_not_called()
            self.assertEqual(json.loads((root / 'review.checkpoint.json').read_text())['state'], 'succeeded')

    def test_failed_operation_records_failure_and_stops_second_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); call = Mock(side_effect=ValueError('real model said revise'))
            evidence = Mock(side_effect=ValueError('not approved'))
            with self.assertRaises(ValueError):
                runner.checkpoint(root, 'review', 'sha', call, evidence)
            with self.assertRaises(runner.Paused):
                runner.checkpoint(root, 'review', 'sha', call, evidence)
            self.assertEqual(call.call_count, 1)

    def test_one_incomplete_product_does_not_block_next_and_cannot_register(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); packet = root / 'packet.json'
            write_json(packet, {'schema_version': 1, 'products': [{'product_id': 'a'}, {'product_id': 'b'}]})
            with patch.object(runner, 'run_product', side_effect=[runner.Paused('awaiting_images', {}), {'product_id': 'b', 'state': 'bound_review_pending'}]) as call:
                rows = runner.run(packet, root / 'out')
            self.assertEqual(call.call_count, 2)
            self.assertEqual([r['state'] for r in rows], ['awaiting_images', 'bound_review_pending'])
            inventory = json.loads((root / 'out/inventory.json').read_text())
            self.assertEqual(inventory['video_submissions'], 0)

    def test_nested_content_hold_stops_before_assets_or_paid_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); evidence = root / 'hold.json'
            write_json(evidence, {'blocks_registration': True, 'reason': 'wrong subject'})
            item = {'product_id': 'a', 'audit_context': [{'path': str(evidence), 'sha256': sha256(evidence)}]}
            with patch.object(runner, 'complete_assets') as assets, patch.object(runner, 'review_manifest') as review:
                with self.assertRaises(runner.Paused) as raised:
                    runner.run_product(item, {}, root / 'out', 'register')
                self.assertEqual(raised.exception.state, 'isolated_known_visual_findings')
                assets.assert_not_called(); review.assert_not_called()

    def test_source_identity_resolution_is_exact_and_keeps_authored_content(self):
        from workflow.storyboard_batch import resolve_source_identity
        from workflow.plan import digest
        plan={'source_packet':{'source_url':'https://esa.int'},'facts':[{'id':'F1'}],'shots':[{'id':'S01'}]}
        parent={'source_packet':{'source_url':'https://xinhuanet.com'}}
        with self.assertRaises(ValueError):resolve_source_identity(plan,parent,None)
        resolution={'candidate_source_packet_digest':digest(plan['source_packet']),
                    'parent_source_packet_digest':digest(parent['source_packet']),
                    'supplemental_sources':[{'archive':'verified-by-source-validator'}]}
        with patch('workflow.revised_sources.validate_revised_sources') as validate:
            result=resolve_source_identity(plan,parent,resolution)
            validate.assert_called_once()
        self.assertEqual(result['source_packet'],parent['source_packet'])
        self.assertEqual(result['facts'],plan['facts']);self.assertEqual(result['shots'],plan['shots'])
        self.assertEqual(plan['source_packet']['source_url'],'https://esa.int')
        resolution['parent_source_packet_digest']='wrong'
        with self.assertRaises(ValueError):resolve_source_identity(plan,parent,resolution)

    def test_runtime_gate_checks_versions_not_just_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); runtime = root / 'runtime.json'
            write_json(runtime, {'pid': 123, 'loaded_media_sha256': 'same', 'story_preflight_lane': True})
            packet = {'renderer_runtime_path': str(runtime), 'required_media_sha256': 'same',
                      'required_media_version': 'whole_action_v1', 'required_story_review_version': 'v3',
                      'required_story_preflight_version': 'v2'}
            with patch.object(runner.os, 'kill'), self.assertRaises(runner.Paused):
                runner.runtime_ready(packet)

    def test_provider_failure_is_not_reported_as_a_content_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / 'keyframe_review.json', {'status': 'review_required',
                       'error_type': 'ModelResultError', 'diagnostic': {'code': 'overloaded_error'}})
            paused = runner.preflight_pause(root, ValueError('needs revision'))
            self.assertEqual(paused.state, 'isolated_review_service_failure')
            self.assertIsNone(paused.detail['content_verdict'])
            self.assertFalse(paused.detail['automatic_retry'])
            write_json(root / 'keyframe_review.json', {'status': 'review_required',
                       'review': {'verdict': 'reject', 'findings': [{'issue': 'wrong subject'}]}})
            self.assertEqual(runner.preflight_pause(root, ValueError()).state, 'isolated_board_revision')

    def test_low_confidence_pass_is_not_a_regeneration_instruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); path = folder / 'keyframe_review.json'
            assessment = {'verdict': 'pass', 'confidence': .78,
                          'findings': [{'severity': 'low', 'issue': 'Motion deferred'}],
                          'not_assessed': ['Generated end state']}
            write_json(path, {'status': 'review_required', 'review': assessment})
            result = runner.preflight_pause(folder, ValueError('hold'))
            self.assertEqual(result.state, 'isolated_review_confidence')
            self.assertEqual(result.detail['required_confidence'], .85)
            self.assertFalse(result.detail['automatic_retry'])
            self.assertEqual(result.detail['deferred_checks'], ['Generated end state'])
            assessment['findings'].append({'severity': 'medium', 'issue': 'Wrong object'})
            write_json(path, {'status': 'review_required', 'review': assessment})
            self.assertEqual(runner.preflight_pause(folder, ValueError()).state, 'isolated_board_revision')

    def test_generation_contract_rejects_two_second_action_before_staging(self):
        plan = {'pacing_policy': 'storyboard_v1', 'shots': [{'id': 'S05a', 'prompt': 'One hand moves',
                'production_mode': 'generated_action', 'duration_seconds': 2.0}]}
        with patch.object(runner.backends, 'assignments', return_value={'S05a': {'remote_root': '/unused', 'capability': 'test'}}):
            with self.assertRaisesRegex(ValueError, 'retime storyboard before dispatch'):
                runner.validate_generation_requests(plan, {})
            plan['shots'][0]['duration_seconds'] = 4.0
            result = runner.validate_generation_requests(plan, {})
            self.assertEqual(result[0]['requested_duration_seconds'], 4)

    def test_real_binding_gets_new_exact_review_and_resume_does_not_call_again(self):
        from tests.test_storyboard_pipeline import film
        from workflow.narrative_gate import review_manifest, CHECKS, require_review
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest = root / 'manifest.json'; draft = root / 'draft.json'
            write_json(manifest, film()); write_json(draft, {'test': True})
            jobs = ImageJobs(root / 'images'); jobs.add(manifest, 'product')
            for _ in range(4): self.complete(root, jobs)
            item = {'product_id': 'product', 'manifest_path': str(manifest), 'manifest_sha256': sha256(manifest),
                    'draft_path': str(draft), 'draft_sha256': sha256(draft), 'image_jobs_dir': str(jobs.folder)}
            client = Mock(return_value=({'verdict': 'pass', 'checks': dict.fromkeys(CHECKS, True), 'issues': []},
                                        {'model': 'test-model', 'usage': {}}))
            def review(m, r): return review_manifest(m, r, client=client)
            with patch.object(runner, 'review_manifest', side_effect=review):
                a = runner.run_product(item, {}, root / 'out', 'review')
                b = runner.run_product(item, {}, root / 'out', 'review')
            self.assertNotEqual(a['manifest_sha256'], item['manifest_sha256'])
            self.assertEqual(a, b); self.assertEqual(client.call_count, 1)
            require_review(a['manifest_path'], a['review_path'])


if __name__ == '__main__':
    unittest.main()
