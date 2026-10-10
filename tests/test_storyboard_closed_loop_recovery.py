"""Durable admission boundaries; no generation calls or production state touched."""
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from workflow.storyboard_batch import register
from workflow.narrative_gate import CHECKS
from workflow.plan import digest, sha256, write_json
from workflow.queue import Queue
from workflow.storyboard_deployment import tick


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.q = Queue(self.root / 'queue.sqlite')
        self.lock = open(self.q.path + '.controller-service.lock', 'a')
        self.addCleanup(self.lock.close)
        with self.q.connect() as c:
            c.execute('CREATE TABLE production_items(item_id TEXT PRIMARY KEY,current_job TEXT)')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Content/asset validators have their own tests. Keep the actual exact-SHA
        # narrative gate, request files, singleton admission and SQLite queue.
        for name, value in [('workflow.storyboard.enabled', True),
                            ('workflow.storyboard.missing_previsuals', [])]:
            self.stack.enter_context(patch(name, return_value=value))
        self.stack.enter_context(patch('workflow.roughcut.validate'))
        self.stack.enter_context(patch('workflow.revised_sources.validate_revised_sources'))
        self.submissions = []
        def submit(queue, manifest, root, backend):
            plan = json.loads(Path(manifest).read_text())
            accepted = queue.submit(plan['id'], digest(plan), plan, [
                {'id': plan['id'] + ':shot', 'stage': 'shot', 'capability': 'cpu',
                 'payload': {}, 'deps': []}])
            self.submissions.append(accepted)
            return {'job_id': plan['id'], 'new': accepted, 'run_dir': str(root / plan['id'])}
        self.stack.enter_context(patch('workflow.runner.submit', side_effect=submit))

    def packet(self, name):
        folder = self.root / 'storyboard_rollout' / 'staged' / name
        plan = {'id': name + '_new', 'root_job_id': name, 'parent_job_id': name + '_old',
                'facts': [{'id': 'F1'}],
                'shots': [{'id': 'S' + str(i)} for i in range(3)],
                'narration': [{'text': 'line ' + str(i)} for i in range(3)],
                'narrative_contract': {
                    'central_question': 'How does this work?', 'story_one_sentence': 'Action leads to result.',
                    'beats': [{'id': 'B' + str(i), 'causal_role': 'action', 'claim': 'supported', 'fact_ids': ['F1']} for i in range(3)],
                    'shots': [{'id': 'S' + str(i), 'beat_id': 'B' + str(i), 'narration_index': i,
                               'narration': 'line ' + str(i), 'fact_ids': ['F1'],
                               'relation_to_previous': 'because', 'visible_action': 'moves',
                               'visual_constraint': 'same subject'} for i in range(3)],
                    'ending': {'result': 'done', 'tradeoff': 'cost', 'comment_prompt': 'why?'}}}
        manifest = folder / 'manifest.json'
        write_json(manifest, plan)
        review = folder / 'review.json'
        write_json(review, {'status': 'completed', 'manifest_sha256': sha256(manifest),
                            'model_receipt': {'model': 'test-reviewer', 'usage': {}},
                            'request_digest': 'test', 'review': {'verdict': 'pass', 'checks': dict.fromkeys(CHECKS, True)}})
        acceptance = {'status': 'ready_for_generation'}
        for key, data in [('manifest', plan), ('visual_review', {'status': 'automatic_review_passed', 'receipt': {'model': 'test'}}),
                          ('edl', {}), ('preview', {})]:
            path = folder / ('frozen_' + key + '.json')
            write_json(path, data)
            acceptance.update({key + '_path': str(path), key + '_sha256': sha256(path)})
        preflight = folder / 'acceptance.json'
        write_json(preflight, acceptance)
        backend = folder / 'backend.json'
        write_json(backend, {'type': 'assets'})
        packet = {'backend_file': str(backend)}
        for key, path in [('manifest', manifest), ('review', review), ('preflight', preflight)]:
            packet.update({key + '_path': str(path), key + '_sha256': sha256(path)})
        self.q.submit(name + '_old', 'parent', {}, [])
        with self.q.connect() as c:
            c.execute('INSERT INTO production_items VALUES(?,?)', (name, name + '_old'))
        return packet

    def test_registration_replay_preserves_inflight_state(self):
        packet = self.packet('product')
        first = register(packet, self.root)
        path = Path(first['path'])
        state = json.loads(path.read_text())
        state.update(state='rendering', job_id='existing-job')
        write_json(path, state)
        second = register(packet, self.root)
        self.assertFalse(second['new'])
        self.assertEqual(second['state'], 'rendering')
        self.assertEqual(json.loads(path.read_text()), state)

    def test_restart_after_queue_commit_before_receipt_does_not_duplicate_tasks(self):
        packet = self.packet('product')
        registered = register(packet, self.root)
        target = Path(registered['path'])
        def crash(path, data):
            if Path(path) == target and data.get('state') == 'rendering':
                raise SystemExit('simulated process death after SQLite commit')
            write_json(path, data)
        with patch('workflow.storyboard_deployment.write_json', side_effect=crash):
            with self.assertRaisesRegex(SystemExit, 'process death'):
                tick(self.q, self.root, self.lock.fileno())
        self.assertEqual(json.loads(target.read_text())['state'], 'approved')
        # Fresh queue instance simulates restart. Durable job/task identity is reused.
        restarted = Queue(self.root / 'queue.sqlite')
        self.assertEqual(tick(restarted, self.root, self.lock.fileno()), ['product_new'])
        self.assertEqual(tick(restarted, self.root, self.lock.fileno()), ['product_new'])
        self.assertEqual(self.submissions, [True, False])
        self.assertEqual(len(restarted.status()), 1)
        with restarted.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM events WHERE event='submitted' AND task_id='product_new'").fetchone()[0], 1)

    def test_runtime_failure_after_submit_is_isolated_without_resubmission(self):
        bad = self.packet('bad')
        good = self.packet('good')
        bad_path = Path(register(bad, self.root)['path'])
        good_path = Path(register(good, self.root)['path'])
        from workflow import runner
        normal_submit = runner.submit
        def uncertain_submit(queue, manifest, root, backend):
            result = normal_submit(queue, manifest, root, backend)
            if result['job_id'] == 'bad_new':
                raise RuntimeError('provider result reconciliation failed after commit')
            return result
        with patch('workflow.runner.submit', side_effect=uncertain_submit):
            self.assertEqual(tick(self.q, self.root, self.lock.fileno()), ['good_new'])
        self.assertEqual(json.loads(bad_path.read_text())['state'], 'isolated')
        self.assertEqual(json.loads(good_path.read_text())['state'], 'rendering')
        self.assertEqual(tick(Queue(self.root / 'queue.sqlite'), self.root, self.lock.fileno()), ['good_new'])
        self.assertEqual(self.submissions, [True, True])
        self.assertEqual(len(self.q.status()), 2)

    def test_changed_story_with_refreshed_outer_checksum_cannot_reuse_old_review(self):
        packet = self.packet('product')
        manifest = Path(packet['manifest_path'])
        plan = json.loads(manifest.read_text())
        plan['title'] = 'A changed story version'
        write_json(manifest, plan)
        packet['manifest_sha256'] = sha256(manifest)
        with self.assertRaisesRegex(ValueError, 'exact manifest'):
            register(packet, self.root)
        self.assertFalse(list((self.root / 'storyboard_rollout/requests').glob('*/deployment_request.json')))
        self.assertEqual(self.q.status(), [])

    def test_failed_product_isolated_while_independent_product_reaches_queue(self):
        bad = self.packet('bad')
        good = self.packet('good')
        bad_path = Path(register(bad, self.root)['path'])
        good_path = Path(register(good, self.root)['path'])
        write_json(Path(bad['review_path']), {'tampered': True})
        self.assertEqual(tick(self.q, self.root, self.lock.fileno()), ['good_new'])
        # deterministic pre-submit refusal -> rejected (P0 taxonomy); the point
        # of this test is that the good product still reaches the queue.
        self.assertEqual(json.loads(bad_path.read_text())['state'], 'rejected')
        self.assertEqual(json.loads(good_path.read_text())['state'], 'rendering')
        self.assertEqual({t['job_id'] for t in self.q.status()}, {'good_new'})
        self.assertEqual(tick(Queue(self.root / 'queue.sqlite'), self.root, self.lock.fileno()), ['good_new'])
        self.assertEqual(self.submissions, [True])


if __name__ == '__main__':
    unittest.main()
