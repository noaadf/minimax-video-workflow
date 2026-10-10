import fcntl
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow.plan import sha256,write_json
from workflow.queue import Queue
from workflow.storyboard_deployment import tick
from workflow.core.failures import WorkflowFailure


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.folder=self.root/'storyboard_rollout';self.folder.mkdir()
        self.q=Queue(self.root/'queue.sqlite')
        self.lock=open(self.q.path+'.controller-service.lock','a');self.addCleanup(self.lock.close)
        fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        write_json(self.folder/'status.json',{'deployed':True,'film_ready':False})

    def request(self,state='approved'):
        paths={}
        for name in ('manifest','review','preflight'):
            path=self.folder/(name+'.json');write_json(path,{})
            paths.update({name+'_path':str(path),name+'_sha256':sha256(path)})
        data={'state':state,**paths};write_json(self.folder/'deployment_request.json',data)
        return data

    def test_foreign_process_cannot_admit_without_singleton_lock(self):
        self.request()
        with self.assertRaisesRegex(ValueError,'singleton'):tick(self.q,self.root,None)
        self.assertEqual(self.q.status(),[])

    def test_changed_review_blocks_before_queue_submission(self):
        self.request();write_json(self.folder/'review.json',{'status':'edited'})
        with self.assertRaisesRegex(ValueError,'evidence changed'):tick(self.q,self.root,self.lock.fileno())
        self.assertEqual(self.q.status(),[])

    def test_new_files_do_not_prove_renderer_adoption(self):
        r=self.request();receipt=self.root/'renderer_runtime.json'
        write_json(receipt,{'pid':1,'story_review_version':'old'})
        r.update(required_story_review_version='new',renderer_runtime_path=str(receipt))
        write_json(self.folder/'deployment_request.json',r)
        with self.assertRaisesRegex(WorkflowFailure,'not loaded') as caught:
            tick(self.q,self.root,self.lock.fileno())
        self.assertEqual(caught.exception.code, 'runtime_not_adopted')
        self.assertEqual(self.q.status(),[])

    def test_failed_preflight_is_not_admitted_even_with_text_review(self):
        self.request()
        with patch('workflow.narrative_gate.require_review'),patch('workflow.storyboard.enabled',return_value=True),\
             patch('workflow.storyboard.missing_previsuals',return_value=[]):
            with self.assertRaisesRegex(ValueError,'preflight must pass'):tick(self.q,self.root,self.lock.fileno())
        self.assertEqual(self.q.status(),[])

    def test_failed_generation_keeps_parent_and_requires_explicit_revision(self):
        r=self.request('rendering');r['job_id']='new';write_json(self.folder/'deployment_request.json',r)
        self.q.submit('new','hash',{},[{'id':'new:S1','stage':'shot','capability':'cpu','payload':{},'deps':[],'max_attempts':1}])
        self.q.finish(self.q.claim(['cpu']),error='action absent')
        self.assertEqual(tick(self.q,self.root,self.lock.fileno()),[])
        self.assertEqual(json.loads((self.folder/'deployment_request.json').read_text())['state'],'isolated')
        self.assertFalse(json.loads((self.folder/'status.json').read_text())['film_ready'])
        self.assertEqual(tick(self.q,self.root,self.lock.fileno()),[])

    def test_complete_export_without_story_checks_is_not_released(self):
        r=self.request('rendering');r['job_id']='new';write_json(self.folder/'deployment_request.json',r)
        tasks=[{'id':'new:'+s,'stage':s,'capability':'cpu','payload':{},'deps':[]} for s in ('quality','export')]
        self.q.submit('new','hash',{},tasks)
        for stage in ('quality','export'):
            self.q.finish(self.q.claim(['cpu'],stages={stage}),result={'delivery_policy':{'ready_for_delivery':True}})
        self.assertEqual(tick(self.q,self.root,self.lock.fileno()),[])
        self.assertFalse(json.loads((self.folder/'status.json').read_text())['film_ready'])

class BatchDeploymentTests(unittest.TestCase):
    def test_bad_product_does_not_starve_other_product(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);q=Queue(root/'q.sqlite');rollout=root/'storyboard_rollout'
            for name in ('bad','good'):
                write_json(rollout/'requests'/name/'deployment_request.json',{'state':'approved'})
            with open(q.path+'.controller-service.lock','a') as lock:
                def one(queue,root,fd,folder):
                    if folder.name=='bad':raise ValueError('stale evidence')
                    return ['good-job'] if folder.name=='good' else []
                with patch('workflow.storyboard_deployment._tick_one',side_effect=one):
                    self.assertEqual(tick(q,root,lock.fileno()),['good-job'])
            self.assertEqual(json.loads((rollout/'requests/bad/deployment_request.json').read_text())['state'],'rejected')

    def test_batch_requires_owner_before_mutating_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);q=Queue(root/'q.sqlite');path=root/'storyboard_rollout/requests/a/deployment_request.json';write_json(path,{'state':'approved'})
            with self.assertRaisesRegex(ValueError,'singleton'):tick(q,root,None)
            self.assertEqual(json.loads(path.read_text())['state'],'approved')
