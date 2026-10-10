import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow.backends import check_lease
from workflow.controller import Controller
from workflow.plan import sha256
from workflow.queue import Queue
from workflow.runner import drain


class SimulatedProcessDeath(BaseException):
    pass


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.q = Queue(self.root / 'queue.sqlite')
        self.plan = {'id': 'parent', 'facts': ['supported fact'], 'narration': ['original'], 'shots': []}
        self.q.submit('parent', 'v1', self.plan, [{'id': 'parent:quality', 'stage': 'quality', 'payload': {}}])
        task = self.q.claim(['cpu'])
        self.q.finish(task, result={'passed': True})
        self.reviews = {}
        self.calls = []
        self.controller = Controller(self.q, self.root, self.submit, self.bundle)

    def bundle(self, queue, jid):
        with queue.connect() as c:
            plan = json.loads(c.execute('SELECT manifest FROM jobs WHERE id=?', (jid,)).fetchone()[0])
        return plan, {'quality': self.reviews.get(jid, {
            'passed': True, 'editorial_status': 'review_required',
            'editorial': {'review': {'verdict': 'review', 'findings': []}}})}

    def submit(self, queue, path, root, backend):
        self.calls.append(path)
        p = json.loads(Path(path).read_text())
        queue.submit(p['id'], p['id'], p, [{'id': p['id'] + ':quality', 'stage': 'quality', 'payload': {}}])

    def config(self, recipes=None, **extra):
        return {'id': 'batch', 'items': [{'id': 'film', 'job_id': 'parent',
                'max_repairs': 2, 'recipes': recipes or [], **extra}]}

    def recipe(self, parent='parent', child='child'):
        p = {**self.plan, 'id': child, 'parent_job_id': parent}
        path = self.root / (child + '.json')
        path.write_text(json.dumps(p))
        return {'parent_job_id': parent, 'manifest_path': str(path), 'sha256': sha256(path), 'reviewed': True}

    def finish(self, jid):
        task = self.q.claim(['cpu'], job_ids=[jid])
        self.q.finish(task, result={'passed': True})

    def passing(self):
        return {'passed': True, 'editorial_status': 'automatic_review_passed',
                'editorial': {'review': {'verdict': 'pass'}}}

    def test_technical_pass_is_quarantined_without_editorial_pass(self):
        self.controller.admit(self.config())
        r = self.controller.tick()['items'][0]
        self.assertEqual(r['state'], 'quarantined')
        self.assertFalse(r['detail']['publication_approved'])

    def test_root_blockers_survive_model_pass(self):
        self.reviews['parent'] = self.passing()
        self.controller.admit(self.config(content_blockers=['upright tokamak']))
        self.assertEqual(self.controller.tick()['items'][0]['state'], 'quarantined')

    def test_pass_only_becomes_ready_for_review(self):
        self.reviews['parent'] = self.passing()
        self.controller.admit(self.config())
        r = self.controller.tick()['items'][0]
        self.assertEqual(r['state'], 'ready_for_review')
        self.assertFalse(r['detail']['publication_approved'])

    def test_duplicate_admission_and_tick_do_not_enqueue_twice(self):
        config = self.config([self.recipe()])
        self.assertTrue(self.controller.admit(config))
        self.assertFalse(self.controller.admit(config))
        for _ in range(3):
            self.controller.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.controller.items()[0]['round'], 1)
        config['items'][0]['max_repairs'] = 3
        with self.assertRaises(ValueError):
            self.controller.admit(config)

    def test_restart_after_enqueue_before_state_commit_recovers_same_job(self):
        self.controller.admit(self.config([self.recipe()]))
        def crash(*args):
            self.submit(*args)
            raise SimulatedProcessDeath()
        self.controller.submitter = crash
        with self.assertRaises(SimulatedProcessDeath):
            self.controller.tick()
        self.assertEqual(self.controller.items()[0]['state'], 'submitting')
        restarted = Controller(self.q, self.root, self.submit, self.bundle)
        restarted.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(restarted.items()[0]['current_job'], 'child')
        self.assertEqual(restarted.items()[0]['round'], 1)

    def test_budget_stops_recursive_repair(self):
        config = self.config([self.recipe(), self.recipe('child', 'grandchild')])
        config['items'][0]['max_repairs'] = 1
        self.controller.admit(config)
        self.controller.tick()
        self.finish('child')
        r = self.controller.tick()['items'][0]
        self.assertEqual(r['detail']['reason'], 'repair_budget_exhausted')
        self.assertEqual(len(self.calls), 1)

    def test_quarantine_resumes_only_with_new_recipe_and_keeps_budget(self):
        self.controller.admit(self.config([self.recipe()]))
        self.controller.tick(); self.finish('child'); self.controller.tick()
        self.assertEqual(self.controller.items()[0]['state'], 'quarantined')
        self.controller.add_recipe('batch', 'film', self.recipe('child', 'grandchild'))
        self.controller.tick()
        self.assertEqual(self.controller.items()[0]['round'], 2)
        self.finish('grandchild'); self.controller.tick()
        with self.assertRaises(ValueError):
            self.controller.add_recipe('batch', 'film', self.recipe('grandchild', 'greatgrandchild'))

    def test_recipe_hash_mutation_is_quarantined(self):
        recipe = self.recipe()
        self.controller.admit(self.config([recipe]))
        Path(recipe['manifest_path']).write_text('{}')
        r = self.controller.tick()['items'][0]
        self.assertEqual(r['state'], 'quarantined')
        self.assertIn('changed', r['detail']['error'])
        self.assertEqual(self.calls, [])

    def exhausted_with_diagnosis(self):
        self.controller.admit(self.config([self.recipe()], max_repairs=1))
        self.controller.tick(); self.finish('child'); self.controller.tick()
        evidence = self.root / 'diagnosis.json'
        evidence.write_text('{"new_source": "closed phone in palm"}')
        return {'parent_job_id': 'child', 'reason': 'New palm endpoint replaces incorrect open endpoint',
                'evidence': [{'path': str(evidence), 'sha256': sha256(evidence)}]}

    def test_diagnosed_correction_is_once_and_preserves_blockers(self):
        diagnosis = self.exhausted_with_diagnosis()
        self.controller.add_recipe('batch', 'film', self.recipe('child', 'grandchild'), diagnosis=diagnosis)
        self.controller.tick(); self.controller.tick()
        self.assertEqual(len(self.calls), 2)
        self.finish('grandchild'); self.controller.tick()
        row = self.controller.items()[0]
        self.assertEqual(row['round'], 2)
        self.assertEqual(json.loads(row['detail'])['reason'], 'repair_budget_exhausted')
        diagnosis['parent_job_id'] = 'grandchild'
        with self.assertRaisesRegex(ValueError, 'already spent'):
            self.controller.add_recipe('batch', 'film', self.recipe('grandchild', 'last'), diagnosis=diagnosis)

    def test_diagnosed_correction_rejects_changed_evidence(self):
        diagnosis = self.exhausted_with_diagnosis()
        Path(diagnosis['evidence'][0]['path']).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'evidence'):
            self.controller.add_recipe('batch', 'film', self.recipe('child', 'grandchild'), diagnosis=diagnosis)
        self.assertEqual(json.loads(self.controller.items()[0]['spec'])['max_repairs'], 1)

    def test_diagnosed_correction_rejects_wrong_parent(self):
        diagnosis = self.exhausted_with_diagnosis()
        diagnosis['parent_job_id'] = 'different'
        with self.assertRaisesRegex(ValueError, 'exact parent'):
            self.controller.add_recipe('batch', 'film', self.recipe('child', 'grandchild'), diagnosis=diagnosis)

    def test_factual_rewrite_is_not_a_visual_repair(self):
        recipe = self.recipe()
        path = Path(recipe['manifest_path'])
        p = json.loads(path.read_text()); p['facts'] = ['invented fact']
        path.write_text(json.dumps(p)); recipe['sha256'] = sha256(path)
        self.controller.admit(self.config([recipe]))
        self.assertIn('protected facts', self.controller.tick()['items'][0]['detail']['error'])

    def test_failed_film_does_not_block_other_film(self):
        self.q.submit('bad', 'v1', {}, [{'id': 'bad:s', 'stage': 'shot', 'payload': {}}])
        task = self.q.claim(['cpu']); self.q.finish(task, error='remote unknown', uncertain=True)
        config = self.config([self.recipe()])
        config['items'].append({'id': 'bad', 'job_id': 'bad'})
        self.controller.admit(config)
        rows = {r['item_id']: r for r in self.controller.tick()['items']}
        self.assertEqual(rows['bad']['state'], 'quarantined')
        self.assertEqual(rows['film']['current_job'], 'child')
        self.assertEqual(len(self.calls), 1)

    def test_scoped_drain_leaves_other_jobs_untouched(self):
        for jid in ['unrelated', 'owned']:
            self.q.submit(jid, jid, {}, [{'id': jid + ':s', 'stage': 'shot', 'payload': {}}])
        with patch('workflow.runner.execute', return_value={'elapsed_seconds': 0}):
            drain(self.q, ['cpu'], 1, job_ids=['owned'])
        states = {t['job_id']: t['status'] for t in self.q.status()}
        self.assertEqual(states['owned'], 'succeeded')
        self.assertEqual(states['unrelated'], 'pending')

    def test_expired_notebook_rejected(self):
        cfg = {'type': 'ascend_ssh', 'expires_at': '2026-10-02T01:00:00+08:00'}
        now = dt.datetime.fromisoformat('2026-10-02T01:01:00+08:00')
        with self.assertRaisesRegex(ValueError, 'expired'):
            check_lease(cfg, now=now)
        with self.assertRaisesRegex(ValueError, 'expiry must'):
            check_lease({'type': 'ascend_ssh'}, now=now, require_expiry=True)


if __name__ == '__main__':
    unittest.main()
