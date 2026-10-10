import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from workflow.diagnosed_revision import compile_timing_revision
from workflow.plan import sha256

class TimingRevisionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.clip=self.root/'clip.mp4';self.clip.write_bytes(b'real-fixture-identity')
  self.db=sqlite3.connect(self.root/'queue.sqlite');self.db.executescript('CREATE TABLE jobs(id TEXT,manifest TEXT);CREATE TABLE production_items(item_id TEXT,current_job TEXT);CREATE TABLE tasks(id TEXT,job_id TEXT,stage TEXT,status TEXT,result TEXT,error TEXT);')
  p={'id':'source','root_job_id':'product','duration_seconds':6,'narration':[{'text':'unchanged','audio_path':'voice.mp3'}],'shots':[{'id':'a','duration_seconds':2,'production_mode':'generated_action'},{'id':'b','duration_seconds':4,'production_mode':'generated_action'}]}
  self.db.execute('INSERT INTO jobs VALUES(?,?)',('source',json.dumps(p)));self.db.execute('INSERT INTO production_items VALUES(?,?)',('product','source'))
  self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',('source:a','source','shot','failed',None,'execute: Generated action cannot fit this slot naturally; retime storyboard before dispatch'))
  self.result={'video_path':str(self.clip),'sha256':{str(self.clip):sha256(self.clip)},'picture_timing':{'source_seconds':4,'fit_policy':'whole_action'}}
  self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',('source:b','source','shot','succeeded',json.dumps(self.result),None));self.db.commit()
  self.probe=patch('workflow.media.duration',return_value=4);self.probe.start();self.validate=patch('workflow.plan.validate');self.validate.start()
 def tearDown(self):self.probe.stop();self.validate.stop();self.db.close();self.tmp.cleanup()
 def run_compile(self):return compile_timing_revision(self.root,'source',{'a':3,'b':3},'a fails before generation; preserve all complete actions')
 def test_replay_reuses_exact_sha_and_preserves_voice(self):
  r=self.run_compile();self.assertEqual(r,self.run_compile());p=json.loads(Path(r['manifest_path']).read_text());self.assertEqual(p['shots'][1]['source_sha256'],sha256(self.clip));self.assertEqual(p['narration'][0]['text'],'unchanged');self.assertFalse(p['diagnostic_timing_revision']['visual_approval'])
 def test_running_never_compiles(self):
  self.db.execute("UPDATE tasks SET status='running',result=NULL WHERE id='source:b'");self.db.commit();self.assertEqual(self.run_compile()['state'],'awaiting_source_settlement');self.assertFalse((self.root/'user_repair30_20261002').exists())
 def test_changed_artifact_rejected(self):
  self.clip.write_bytes(b'changed');self.assertRaisesRegex(ValueError,'changed',self.run_compile)
 def test_unrelated_failure_not_retried(self):
  self.db.execute("UPDATE tasks SET error='GPU OOM' WHERE id='source:a'");self.db.commit();self.assertRaisesRegex(ValueError,'pre-dispatch',self.run_compile)
 def test_cumulative_speed_checked(self):
  self.result['picture_timing']['source_seconds']=6;self.db.execute("UPDATE tasks SET result=? WHERE id='source:b'",(json.dumps(self.result),));self.db.commit();self.assertRaisesRegex(ValueError,'Cumulative',self.run_compile)
 def test_total_cannot_change(self):
  self.assertRaisesRegex(ValueError,'whole-film',compile_timing_revision,self.root,'source',{'a':3,'b':4},'diagnosis')
 def test_visual_failure_needs_real_bound_evidence(self):
  rev=self.root/'review.json';rev.write_text(json.dumps({'video_path':str(self.clip),'video_sha256':sha256(self.clip),'sample_times':[0,2,3.9],'major_findings':['internal cut'],'status':'reuse_requires_editorial_diagnosis'}))
  r=compile_timing_revision(self.root,'source',{'a':3,'b':3},'mixed diagnosis',{'b':{'prompt':'fixed camera continuous action no cut','review_path':str(rev)}})
  p=json.loads(Path(r['manifest_path']).read_text());self.assertNotIn('source_path',p['shots'][1]);self.assertEqual(p['shots'][1]['prompt'],'fixed camera continuous action no cut');self.assertIn('b',p['diagnostic_timing_revision']['visual_repairs'])
 def test_visual_failure_missing_tail_rejected(self):
  rev=self.root/'review.json';rev.write_text(json.dumps({'video_path':str(self.clip),'video_sha256':sha256(self.clip),'sample_times':[0,2],'major_findings':['internal cut'],'status':'reuse_requires_editorial_diagnosis'}))
  self.assertRaisesRegex(ValueError,'first/middle/end',compile_timing_revision,self.root,'source',{'a':3,'b':3},'mixed',{'b':{'prompt':'fixed camera continuous no cut','review_path':str(rev)}})
 def test_registered_child_may_compile_without_moving_card(self):
  row=json.loads(self.db.execute("SELECT manifest FROM jobs").fetchone()[0]);row['parent_job_id']='prior';self.db.execute("UPDATE jobs SET manifest=?",(json.dumps(row),));self.db.execute("UPDATE production_items SET current_job='prior'");self.db.commit();r=self.run_compile();self.assertEqual(r['parent_job_id'],'prior');self.assertEqual(self.db.execute("SELECT current_job FROM production_items").fetchone()[0],'prior')
 def test_unrelated_current_revision_rejected(self):
  self.db.execute("UPDATE production_items SET current_job='other'");self.db.commit();self.assertRaisesRegex(ValueError,'registered child',self.run_compile)
if __name__=='__main__':unittest.main()
