import json,sqlite3,tempfile,unittest
from pathlib import Path
from workflow.diagnosed_revision import compile_revision
from workflow.plan import sha256,write_json

class DiagnosticRevisionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.plan={'id':'p-v1','root_job_id':'p','narration':[{'text':'unchanged'}],'shots':[{'id':x,'prompt':'old','duration_seconds':4} for x in ['S01','S02']]}
  self.db=sqlite3.connect(self.root/'queue.sqlite');self.addCleanup(self.db.close)
  self.db.executescript('CREATE TABLE jobs(id,manifest);CREATE TABLE production_items(item_id,current_job);CREATE TABLE tasks(id,job_id,stage,status,result);')
  self.db.execute('INSERT INTO jobs VALUES(?,?)',('p-v1',json.dumps(self.plan)));self.db.execute('INSERT INTO production_items VALUES(?,?)',('p','p-original'))
  self.review={'job_id':'p-v1','shots':{}}
  for sid in ['S01','S02']:
   asset=self.root/(sid+'.mp4');asset.write_bytes(sid.encode());self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?)',('p-v1:'+sid,'p-v1','shot','succeeded',json.dumps({'video_path':str(asset)})))
   self.review['shots'][sid]={'sha256':sha256(asset),'verdict':'pass' if sid=='S01' else 'fail','first_middle_last_checked':True,'finding':'coherent' if sid=='S01' else 'jump cut'}
  self.db.commit();self.r=self.root/'review.json';write_json(self.r,self.review);self.patch={'S02':{'prompt':'Single uninterrupted dolly, preserve architecture'}}
 def test_reuse_identity_replay_and_budget(self):
  a=compile_revision(self.root,'p-v1',self.r,self.patch);b=compile_revision(self.root,'p-v1',self.r,self.patch);self.assertEqual(a,b)
  p=json.loads(Path(a['manifest_path']).read_text());self.assertEqual(p['parent_job_id'],'p-original');self.assertEqual(p['diagnostic_revision']['source_job_id'],'p-v1');self.assertEqual(p['narration'],self.plan['narration']);self.assertEqual(p['shots'][0]['source_fit_policy'],'whole_action');self.assertNotIn('source_path',p['shots'][1])
  with self.assertRaisesRegex(ValueError,'budget'):compile_revision(self.root,'p-v1',self.r,{'S02':{'prompt':'another attempt'}})
 def test_incomplete_or_changed_review_rejected(self):
  self.review['shots'].pop('S01');write_json(self.r,self.review)
  with self.assertRaises(ValueError):compile_revision(self.root,'p-v1',self.r,self.patch)
 def test_failed_peer_and_tampered_asset_rejected(self):
  (self.root/'S01.mp4').write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'SHA'):compile_revision(self.root,'p-v1',self.r,self.patch)
 def test_unsettled_and_false_pass_rejected(self):
  self.db.execute("UPDATE tasks SET status='running' WHERE id='p-v1:S02'");self.db.commit()
  with self.assertRaisesRegex(ValueError,'settled'):compile_revision(self.root,'p-v1',self.r,self.patch)
 def test_manifest_tamper_replay_rejected(self):
  a=compile_revision(self.root,'p-v1',self.r,self.patch);Path(a['manifest_path']).write_text('{}')
  with self.assertRaisesRegex(ValueError,'changed'):compile_revision(self.root,'p-v1',self.r,self.patch)
 def test_draft_amend_before_review_only(self):
  from workflow.diagnosed_revision import amend_unreviewed_draft
  a=compile_revision(self.root,'p-v1',self.r,self.patch)
  b=amend_unreviewed_draft(a,{'S02':{'camera':'continuous dolly'}})
  self.assertNotEqual(a['manifest_sha256'],b['manifest_sha256'])
  self.assertEqual(len(list(Path(b['manifest_path']).parent.glob('superseded_draft*'))),1)
  (Path(b['manifest_path']).parent/'preflight').mkdir()
  with self.assertRaisesRegex(ValueError,'immutable'):amend_unreviewed_draft(b,{'S02':{'camera':'other'}})
 def test_registration_inherits_executor_and_current_parent(self):
  from workflow.diagnosed_revision import review_and_register
  from unittest.mock import patch
  a=compile_revision(self.root,'p-v1',self.r,self.patch)
  (self.root/'user_repair30_20261002/batch_story_v3').mkdir(parents=True)
  folder=self.root/'storyboard_rollout/requests/source';folder.mkdir(parents=True)
  backend=self.root/'backend.json';write_json(backend,{'type':'pool'})
  write_json(folder/'deployment_request.json',{'job_id':'p-v1','backend_file':str(backend),'required_media_sha256':'verified'})
  self.db.execute("UPDATE production_items SET current_job='p-v1'");self.db.commit()
  with patch('workflow.storyboard_batch.one',return_value={'state':'ready_for_staging','acceptance_path':'pass.json'}),patch('workflow.storyboard_batch.stage',return_value={'manifest_path':a['manifest_path']}) as st,patch('workflow.storyboard_batch.register',return_value={'state':'rendering','new':False}) as reg:
   result=review_and_register(a,self.root)
   self.assertEqual(st.call_args.args[2],'p-v1')
   self.assertEqual(reg.call_args.args[0]['backend_file'],str(backend))
   self.assertEqual(reg.call_args.args[0]['required_media_sha256'],'verified')
   self.assertEqual(result['state'],'rendering')
 def test_missing_backend_recovery_preserves_failed_receipt_and_hold(self):
  from workflow.diagnosed_revision import review_and_register
  from unittest.mock import patch
  a=compile_revision(self.root,'p-v1',self.r,self.patch)
  (self.root/'user_repair30_20261002/batch_story_v3').mkdir(parents=True)
  source=self.root/'storyboard_rollout/requests/source';source.mkdir(parents=True)
  backend=self.root/'backend.json';write_json(backend,{'type':'pool'})
  write_json(source/'deployment_request.json',{'job_id':'p-v1','backend_file':str(backend)})
  failed=self.root/'storyboard_rollout/requests/new/deployment_request.json';failed.parent.mkdir()
  write_json(failed,{'state':'isolated','error':"'backend_file'"})
  hold=self.root/'user_repair30_20261002/delivery_holds/p-v1.json';hold.parent.mkdir();write_json(hold,{'status':'blocked'})
  with patch('workflow.storyboard_batch.one',return_value={'state':'ready_for_staging','acceptance_path':'pass.json'}),patch('workflow.storyboard_batch.stage',return_value={'manifest_path':a['manifest_path']}),patch('workflow.storyboard_batch.register',return_value={'state':'isolated','new':False,'path':str(failed)}):
   result=review_and_register(a,self.root)
  self.assertTrue(result['recovered_pre_admission_packet'])
  self.assertEqual(json.loads(failed.read_text())['state'],'approved')
  self.assertTrue((failed.parent/'pre_admission_failure.json').exists())
  self.assertEqual(json.loads(hold.read_text())['status'],'blocked')
