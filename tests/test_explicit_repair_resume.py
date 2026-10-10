import tempfile,unittest,copy
from pathlib import Path
from workflow.plan import write_json,sha256
from scripts.run_user_repair30 import verified_narration_assets,explicit_repair_round
from scripts.resume_user_repair import validate_resume
class RevisionTests(unittest.TestCase):
 def test_current_text_and_voice_required_for_supplied_audio(self):
  with tempfile.TemporaryDirectory() as d:
   f=Path(d)/'B1.mp3';f.write_bytes(b'voice');write_json(f.with_suffix('.json'),{'request':{'text':'new text','voice':{'name':'speaker'}},'sha256':sha256(f)})
   p={'voice':{'name':'speaker'},'narration':[{'text':'new text','audio_path':str(f)}]};self.assertEqual(len(verified_narration_assets(p)),1)
   p['narration'][0]['text']='changed text'
   with self.assertRaises(ValueError):verified_narration_assets(p)
   p['narration'][0]['text']='new text';f.write_bytes(b'old voice')
   with self.assertRaises(ValueError):verified_narration_assets(p)
 def test_new_text_without_explicit_audio_has_no_parent_reuse(self):
  self.assertEqual(verified_narration_assets({'narration':[{'text':'new text'}]}),[])
 def args(self):
  return ({'state':'isolated','request_id':'r','job_id':'old','new_job_id':'r1','product_id':'p'},{'id':'r','status':'processing','job_id':'old'},'r1',{'id':'r2','root_job_id':'p','parent_job_id':'old','repair_round':2},'new method and diagnosis')
 def test_explicit_resume_requires_new_version_and_current_cas(self):
  a=self.args();validate_resume(*a)
  for index,value in [(2,'other'),(4,'')]:
   b=list(self.args());b[index]=value
   with self.assertRaises(ValueError):validate_resume(*b)
  a=self.args();a[3]['id']='r1'
  with self.assertRaises(ValueError):validate_resume(*a)
 def test_old_revision_and_nonisolated_cannot_resume(self):
  a=self.args();a[3]['repair_round']=1
  with self.assertRaises(ValueError):validate_resume(*a)
  a=self.args();a[0]['state']='rendering'
  with self.assertRaises(ValueError):validate_resume(*a)
 def test_round_not_hardcoded(self):
  self.assertEqual(explicit_repair_round({'repair_round':2}),2)
  with self.assertRaises(ValueError):explicit_repair_round({'repair_round':0})
 def test_approval_change_cannot_automatically_retry_isolation(self):
  from scripts.run_user_repair30 import automatically_preparable
  request={'product_id':'p','request_id':'r'}
  self.assertFalse(automatically_preparable(request,{'r':{'state':'isolated'}},{'p':{'version':'new'}}))
  self.assertTrue(automatically_preparable(request,{}, {'p':{'version':'new'}}))
