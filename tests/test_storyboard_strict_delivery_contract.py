import copy,json,fcntl,tempfile,unittest
from pathlib import Path
from workflow.plan import write_json
from workflow.queue import Queue
from workflow.delivery_policy import resolve,gate_state
from workflow.storyboard_deployment import tick

class StrictContractTests(unittest.TestCase):
 def quality(self):
  return {'passed':True,'storyboard_required':True,'shot_review':{'status':'sampled_checks_passed'},'speech':{'status':'transcript_match','review_required':False},'editorial_status':'automatic_review_passed','editorial':{'status':'automatic_review_passed','receipt':{'model':'real-model'},'review':{'verdict':'pass','findings':[]}}}
 def test_missing_policy_is_evaluated_strictly_without_mutating(self):
  q=self.quality();old=copy.deepcopy(q);p=resolve(q,{})
  self.assertTrue(p['ready_for_delivery']);self.assertEqual(p['profile'],'strict_v1');self.assertEqual(q,old)
 def test_throughput_advisory_does_not_become_strict_pass(self):
  # Owner-relaxed 2026-10-06: pending editorial approval is advisory on both profiles.
  q=self.quality();q['editorial_status']=q['editorial']['status']='review_required';q['editorial']['review']={'verdict':'review','findings':[{'category':'visual_quality','severity':'medium','issue':'style concern'}]}
  self.assertTrue(resolve(q,{'quality_profile':'throughput_v1'})['ready_for_delivery'])
  self.assertTrue(resolve(q,{})['ready_for_delivery']);self.assertEqual(gate_state(q,resolve(q,{})),'ready')
 def test_no_api_or_shot_or_speech_evidence_never_passes(self):
  # Owner-relaxed 2026-10-06: api/shot evidence is advisory; missing speech still holds.
  for change,expect_ready in [('api',True),('shot',True),('speech',False)]:
   q=self.quality()
   if change=='api':q['editorial']={'status':'review_required','error_type':'ModelResultError'}
   if change=='shot':q['shot_review']={'status':'review_required','shots':[{'review_execution_status':'failed','error_type':'TimeoutError'}]}
   if change=='speech':q.pop('speech')
   with self.subTest(change=change):
    self.assertEqual(resolve(q,{})['ready_for_delivery'],expect_ready)
    self.assertEqual(gate_state(q,resolve(q,{})),'ready' if expect_ready else 'awaiting_review')
 def test_actual_defects_remain_failed(self):
  q=self.quality();q['editorial']['review']={'verdict':'reject','findings':[{'category':'geometry','severity':'high','issue':'paper duplicated'}]}
  self.assertEqual(gate_state(q,resolve(q,{})),'failed')
 def fixture(self,qvalue):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name);q=Queue(root/'q.sqlite');folder=root/'storyboard_rollout';folder.mkdir()
  manifest=folder/'manifest.json';write_json(manifest,{'root_job_id':'product','parent_job_id':'old'})
  write_json(folder/'deployment_request.json',{'state':'rendering','job_id':'new','manifest_path':str(manifest)})
  write_json(folder/'status.json',{'film_ready':False});video=root/'video.mp4';video.write_bytes(b'verified-test-export')
  q.submit('new','hash',{},[{'id':'new:'+s,'stage':s,'capability':'cpu','payload':{},'deps':[]} for s in ('quality','export')])
  for stage,result in [('quality',qvalue),('export',{'video_path':str(video)})]:q.finish(q.claim(['cpu'],stages={stage}),result=result)
  lock=open(q.path+'.controller-service.lock','a');self.addCleanup(lock.close);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  return root,q,folder,lock
 def test_pending_api_does_not_reset_tasks_or_isolate_content(self):
  # Owner-relaxed 2026-10-06: pending editorial API no longer holds delivery; the
  # request completes without resetting finished tasks or isolating content.
  quality=self.quality();quality['editorial']={'status':'review_required','error_type':'ModelResultError'}
  root,q,f,lock=self.fixture(quality)
  with q.connect() as c:
   c.execute('CREATE TABLE production_items(item_id TEXT,current_job TEXT,state TEXT,detail TEXT,updated REAL)');c.execute("INSERT INTO production_items VALUES('product','old','running','{}',0)")
  before=q.status();tick(q,root,lock.fileno());tick(q,root,lock.fileno())
  self.assertEqual(q.status(),before);self.assertEqual(json.loads((f/'deployment_request.json').read_text())['state'],'completed');self.assertTrue(json.loads((f/'status.json').read_text())['film_ready'])
 def test_strict_legacy_quality_finishes_and_retains_original_receipt(self):
  root,q,f,lock=self.fixture(self.quality())
  with q.connect() as c:
   c.execute('CREATE TABLE production_items(item_id TEXT,current_job TEXT,state TEXT,detail TEXT,updated REAL)');c.execute("INSERT INTO production_items VALUES('product','old','running','{}',0)")
  before=q.status();tick(q,root,lock.fileno())
  self.assertEqual(q.status(),before);self.assertEqual(json.loads((f/'deployment_request.json').read_text())['state'],'completed');self.assertTrue(json.loads((f/'status.json').read_text())['film_ready'])
