import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow.plan import write_json
from workflow.queue import Queue
from workflow.runner import drain_partitioned, submit, execute, StageError


class SpeechQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.q=Queue(self.root/'queue.sqlite')
    def add(self, jid, specs):
        self.q.submit(jid,jid,{},[{'id':jid+':'+n,'stage':stage,'capability':'cpu',
            'payload':{},'deps':[jid+':'+d for d in deps]} for n,stage,deps in specs])
    def test_speech_serial_across_queue_connections_but_other_cpu_work_runs(self):
        self.add('a',[('s','speech',[])]);self.add('b',[('s','speech',[]),('c','compose',[])])
        a=self.q.claim(['cpu'],stages={'speech'})
        second=Queue(self.q.path)
        self.assertIsNone(second.claim(['cpu'],stages={'speech'}))
        c=second.claim(['cpu'],stages={'compose'});self.assertIsNotNone(c)
        second.finish(c,result={'ok':True});self.q.finish(a,result={'ok':True})
        self.assertEqual(second.claim(['cpu'],stages={'speech'})['id'],'b:s')
    def test_expired_speech_lease_requires_reconciliation_before_new_model(self):
        self.add('a',[('s','speech',[]),('q','quality',['s'])]);self.add('b',[('s','speech',[])])
        self.q.claim(['cpu'],stages={'speech'},ttl=-1)
        self.assertIsNone(self.q.claim(['cpu'],stages={'speech'}))
        status={t['id']:t['status'] for t in self.q.status()}
        self.assertEqual(status,{'a:s':'reconcile','a:q':'blocked','b:s':'pending'})
    def test_speech_lane_does_not_block_composition_and_export(self):
        self.add('a',[('s','speech',[]),('q','quality',['s']),('e','export',['q'])])
        self.add('b',[('c','compose',[]),('s','speech',['c']),('q','quality',['s']),('e','export',['q'])])
        composed=threading.Event();exported=threading.Event()
        def run(queue,task):
            if task['id']=='a:s':self.assertTrue(composed.wait(3),'speech blocked compose')
            if task['id']=='b:c':composed.set()
            if task['id']=='b:s':self.assertTrue(exported.wait(3),'speech blocked export')
            if task['id']=='a:e':exported.set()
            return {'elapsed_seconds':0}
        with patch('workflow.runner.execute',run):drain_partitioned(self.q,['cpu'],media_workers=1,review_workers=1)
        self.assertEqual({t['status'] for t in self.q.status()},{'succeeded'})
    def test_new_graph_requires_speech_before_quality(self):
        plan=json.loads(Path('workflow/examples/smoke_bridge.json').read_text())
        for shot in plan['shots']:shot['source_path']=str(self.root/'source.mp4')
        path=self.root/'plan.json';write_json(path,plan)
        with patch('workflow.runner.freeze_assets',side_effect=lambda p,base:p):
            submit(self.q,path,self.root)
        with self.q.connect() as c:
            row=c.execute("select deps,payload from tasks where stage='quality'").fetchone()
            payload=json.loads(row['payload'])
            self.assertIn(payload['data']['speech'],json.loads(row['deps']))
            self.assertEqual(c.execute("select count(*) from tasks where stage='speech'").fetchone()[0],1)
    def test_busy_is_bounded_retry_without_certifying_audio(self):
        manifest=self.root/'manifest.json';write_json(manifest,{'narration':[{'text':'test'}]})
        task={'id':'a:speech','token':'one','capability':'cpu','stage':'speech','deps':['a:compose'],
              'payload':{'plan':{'input_sha256':{}},'folder':str(self.root),'data':{'compose':'a:compose'}}}
        with patch.object(self.q,'results',return_value={'a:compose':{'manifest_path':str(manifest),'video_path':'unused'}}),\
             patch('workflow.speech_gate.screen',return_value={'status':'speech_deferred','reason':'busy'}):
            with self.assertRaises(StageError) as error:execute(self.q,task)
        self.assertTrue(error.exception.retryable)
