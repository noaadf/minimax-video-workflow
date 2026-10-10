import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from workflow.queue import Queue, LostLease
from workflow.plan import validate


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.q=Queue(Path(self.tmp.name)/'queue.sqlite')
    def tearDown(self):self.tmp.cleanup()
    def add(self, tasks, jid='job'):
        return self.q.submit(jid,'digest',{},[{'id':k,'stage':'test','payload':{},'deps':deps,'capability':cap} for k,deps,cap in tasks])
    def test_dependency_gate(self):
        self.add([('a',[],'cpu'),('b',['a'],'cpu')])
        a=self.q.claim(['cpu']);self.assertEqual(a['id'],'a')
        self.assertIsNone(self.q.claim(['cpu']))
        self.q.finish(a,result={'ok':True})
        self.assertEqual(self.q.claim(['cpu'])['id'],'b')
    def test_idempotent_submit_and_manifest_conflict(self):
        self.assertTrue(self.add([('a',[],'cpu')]))
        self.assertFalse(self.add([('a',[],'cpu')]))
        with self.assertRaises(ValueError):self.q.submit('job','new-digest',{},[])
        self.assertEqual(len(self.q.status()),1)
    def test_concurrent_claim_one_winner(self):
        self.add([('a',[],'cpu')])
        winners=[]
        ts=[threading.Thread(target=lambda:winners.append(self.q.claim(['cpu']))) for _ in range(8)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(sum(t is not None for t in winners),1)
    def test_accelerator_slot_exclusive(self):
        self.add([('a',[],'npu0-7'),('b',[],'npu0-7'),('c',[],'npu8-15')])
        a=self.q.claim(['npu0-7']);self.assertIsNone(self.q.claim(['npu0-7']))
        self.assertEqual(self.q.claim(['npu8-15'])['id'],'c')
        self.q.finish(a,result={'ok':True});self.assertEqual(self.q.claim(['npu0-7'])['id'],'b')
    def test_batch_rotates_between_films_without_sharing_device(self):
        self.add([('a1',[],'npu'),('a2',[],'npu')],jid='film_a')
        self.add([('b1',[],'npu'),('b2',[],'npu')],jid='film_b')
        order=[]
        for _ in range(4):
            task=self.q.claim(['npu']);order.append(task['id'])
            self.assertIsNone(self.q.claim(['npu']))
            self.q.finish(task,result={'ok':True})
        self.assertEqual(order,['a1','b1','a2','b2'])
    def test_expired_lease_cannot_publish(self):
        self.add([('a',[],'cpu')])
        a=self.q.claim(['cpu'],ttl=-1)
        with self.assertRaises(LostLease):self.q.finish(a,result={'stale':True})
        b=self.q.claim(['cpu']);self.assertNotEqual(a['token'],b['token'])
        with self.assertRaises(LostLease):self.q.heartbeat(a['id'],a['token'])
        self.q.finish(b,result={'current':True})
    def test_expired_accelerator_requires_reconciliation(self):
        self.add([('a',[],'npu'),('b',[],'npu')])
        self.q.claim(['npu'],ttl=-1)
        self.assertIsNone(self.q.claim(['npu']))
        self.assertEqual(self.q.status()[0]['status'],'reconcile')
    def test_unknown_remote_outcome_holds_accelerator(self):
        self.add([('a',[],'npu'),('b',[],'npu')])
        a=self.q.claim(['npu']);self.q.finish(a,error='outcome unknown',uncertain=True)
        self.assertIsNone(self.q.claim(['npu']))
    def test_retry_is_bounded(self):
        self.add([('a',[],'cpu')])
        for i in range(3):
            a=self.q.claim(['cpu'],now=time.time()+1000)
            self.q.finish(a,error='transient',retryable=True)
        self.assertEqual(self.q.status()[0]['status'],'failed')
    def test_failed_dependency_blocks_transitively(self):
        self.add([('a',[],'cpu'),('b',['a'],'cpu'),('c',['b'],'cpu')])
        self.q.finish(self.q.claim(['cpu']),error='permanent')
        self.assertIsNone(self.q.claim(['cpu']))
        self.assertEqual([x['status'] for x in self.q.status()],['failed','blocked','blocked'])
    def test_missing_dependency_rejected(self):
        with self.assertRaises(ValueError):self.add([('b',['absent'],'cpu')])
    def test_cycle_rejected(self):
        with self.assertRaises(ValueError):self.add([('a',['b'],'cpu'),('b',['a'],'cpu')])


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.p=json.loads(Path('workflow/examples/smoke_bridge.json').read_text())
    def test_smoke_valid(self):validate(self.p)
    def test_unknown_claim_rejected(self):
        self.p['narration'][0]['fact_ids']=['invented']
        with self.assertRaises(ValueError):validate(self.p)
    def test_no_music_tail_rejected(self):
        self.p['narration'][-1]['end']=30
        with self.assertRaises(ValueError):validate(self.p)
    def test_bad_total_rejected(self):
        self.p['shots'][0]['duration_seconds']=1
        with self.assertRaises(ValueError):validate(self.p)
    def test_missing_angles_rejected(self):
        for s in self.p['shots']:s['camera']='wide'
        with self.assertRaises(ValueError):validate(self.p)

if __name__=='__main__':unittest.main()
