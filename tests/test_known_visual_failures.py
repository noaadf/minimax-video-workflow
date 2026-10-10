import copy,importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from workflow.plan import sha256,write_json
spec=importlib.util.spec_from_file_location('known_audit',Path(__file__).resolve().parents[1]/'research/user_repair30_20261002/visual_audit.py');audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
class KnownFailureTests(unittest.TestCase):
 def fixture(self,root):
  v=root/'video.mp4';v.write_bytes(b'video');contact=root/'contact.jpg';contact.write_bytes(b'contact')
  f={'job_id':'j','shot_id':'S03','video_path':str(v),'video_sha256':sha256(v),'contact_path':str(contact),'contact_sha256':sha256(contact),'verdict':'reject','severity':'high','category':'count_mutation','sample_seconds':[0,1.5,3],'observation':'A box appears without source','reviewer':'reviewer','delivery_block':True}
  path=root/'output/production/user_repair30_20261002/known_material_failures/j.json';write_json(path,{'job_id':'j','findings':[f]})
  tasks={'S03':{'video_path':str(v),'sha256':{str(v):sha256(v)}}};rec={'status':'sampled_checks_passed','review':{'shots':[{'shot_id':'S03','verdict':'pass','confidence':.95,'findings':[]}]}}
  return f,path,tasks,rec
 def test_valid_failure_overrides_pass_preserves_model_and_no_duplicates(self):
  with tempfile.TemporaryDirectory() as d,patch.object(audit,'ROOT',Path(d)):
   f,p,t,r=self.fixture(Path(d));self.assertEqual(audit.merge_known(r,'j',t),{'S03'});self.assertEqual(r['review']['shots'][0]['verdict'],'reject');self.assertEqual(r['model_review']['shots'][0]['verdict'],'pass');self.assertEqual(r['status'],'repair_or_review_required');audit.merge_known(r,'j',t);self.assertEqual(len(r['review']['shots'][0]['findings']),1)
 def test_different_old_video_hash_not_applied(self):
  with tempfile.TemporaryDirectory() as d,patch.object(audit,'ROOT',Path(d)):
   f,p,t,r=self.fixture(Path(d));f['video_sha256']='a'*64;write_json(p,{'job_id':'j','findings':[f]});self.assertEqual(audit.merge_known(r,'j',t),set());self.assertEqual(r['review']['shots'][0]['verdict'],'pass');self.assertEqual(len(r['known_material_failures']['stale_ignored']),1)
 def test_broken_contact_or_unknown_job_or_shot_fails_closed(self):
  for key,value in [('contact_sha256','a'*64),('job_id','other'),('shot_id','unknown'),('category','unknown')]:
   with self.subTest(key=key),tempfile.TemporaryDirectory() as d,patch.object(audit,'ROOT',Path(d)):
    f,p,t,r=self.fixture(Path(d));f[key]=value;write_json(p,{'job_id':'j','findings':[f]})
    with self.assertRaises(ValueError):audit.merge_known(r,'j',t)
 def test_preexisting_audit_failure_not_promoted(self):
  with tempfile.TemporaryDirectory() as d,patch.object(audit,'ROOT',Path(d)):
   f,p,t,r=self.fixture(Path(d));r['status']='audit_failed';audit.merge_known(r,'j',t);self.assertEqual(r['status'],'audit_failed')
 def test_all_shots_reviewed_but_known_failure_skips_extra_api(self):
  import sqlite3
  from PIL import Image
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);f,p,t,r=self.fixture(root);v=root/'other.mp4';v.write_bytes(b'other');db=root/'output/production/queue.sqlite'
   with sqlite3.connect(db) as c:
    c.execute('create table jobs(id,manifest)');c.execute('create table tasks(id,result,job_id,stage,status)');c.execute('insert into jobs values(?,?)',('j',json.dumps({'shots':[{'id':'S03','duration_seconds':3},{'id':'S04','duration_seconds':3}]})))
    for sid,path in [('S03',Path(f['video_path'])),('S04',v)]:c.execute('insert into tasks values(?,?,?,?,?)',('j:'+sid,json.dumps({'video_path':str(path),'sha256':{str(path):sha256(path)}}),'j','shot','succeeded'))
   answer={'shots':[{'shot_id':'S03','verdict':'pass','confidence':.99,'findings':[]},{'shot_id':'S04','verdict':'review','confidence':.8,'findings':[]}]}
   def frame(video,t,out):Image.new('RGB',(360,640)).save(out);return t
   with patch.object(audit,'ROOT',root),patch.object(audit,'OUT',root/'audit'),patch.object(audit,'frame',side_effect=frame),patch.object(audit,'ask_json',return_value=(answer,{'model':'test','usage':{}})) as ask,patch.object(audit,'review_shot',return_value={}) as extra:
    rec=audit.run({'version_job_id':'j','product_id':'p','title':'title'});self.assertEqual(len(ask.call_args.args[1]['shots']),2);self.assertEqual(extra.call_count,1);self.assertEqual(extra.call_args.args[1]['id'],'S04');self.assertEqual(rec['review']['shots'][0]['verdict'],'reject');self.assertEqual(rec['model_review']['shots'][0]['verdict'],'pass')
