import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow.queue import Queue
from workflow.runner import drain_partitioned


class WorkerLaneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.q = Queue(Path(self.tmp.name) / 'queue.sqlite')
    def tearDown(self):
        self.tmp.cleanup()
    def add(self, jid, specs):
        self.q.submit(jid, jid, {}, [dict(id=jid+':'+name, stage=stage, capability=cap,
            deps=[jid+':'+dep for dep in deps], payload={}) for name,stage,cap,deps in specs])
    def test_export_runs_while_next_review_waits(self):
        self.add('a', [('q','quality','cpu',[]), ('e','export','cpu',['q'])])
        self.add('b', [('q','quality','cpu',[])])
        exported = threading.Event()
        seen = []
        def execute(queue, task):
            if task['id'] == 'b:q':
                if not exported.wait(4):
                    raise AssertionError('review blocked the export lane')
            if task['id'] == 'a:e':
                exported.set()
            seen.append(task['id'])
            return {'elapsed_seconds':0}
        with patch('workflow.runner.execute', execute):
            drain_partitioned(self.q, ['cpu'])
        self.assertEqual({t['status'] for t in self.q.status()}, {'succeeded'})
        self.assertLess(seen.index('a:e'), seen.index('b:q'))
    def test_lane_dependencies_and_job_isolation(self):
        specs=[('s','shot','npu',[]),('c','compose','cpu',['s']),('q','quality','cpu',['c']),('e','export','cpu',['q'])]
        self.add('allowed',specs); self.add('other',specs)
        with patch('workflow.runner.execute',return_value={'elapsed_seconds':0}):
            drain_partitioned(self.q,['cpu','npu'],job_ids=['allowed'])
        for t in self.q.status():
            self.assertEqual(t['status'],'succeeded' if t['job_id']=='allowed' else 'pending')
    def test_missing_capability_returns_without_spinning(self):
        self.add('a',[('s','shot','npu',[]),('q','quality','cpu',['s'])])
        with patch('workflow.runner.execute') as execute:
            drain_partitioned(self.q,['cpu'])
        execute.assert_not_called()
    def test_stage_filter_does_not_claim_other_lane(self):
        self.add('a',[('q','quality','cpu',[]),('c','compose','cpu',[])])
        self.assertEqual(self.q.claim(['cpu'],stages={'compose'})['stage'],'compose')
