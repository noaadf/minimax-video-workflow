import json,tempfile,unittest
from pathlib import Path
from workflow.plan import write_json,sha256
from workflow.audit_resolution import resolved_shots

class ResolutionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.video=self.root/'video';self.video.write_bytes(b'media');self.frame=self.root/'frame';self.frame.write_bytes(b'frame')
  self.audit=self.root/'audit.json';self.res=self.root/'resolution.json'
  self.record={'job_id':'job','finished_at':1,'status':'review_required','receipt':{'model':'test'},'review':{'shots':[{'shot_id':'S1','verdict':'review','findings':[{'severity':'medium','category':'unknown'}]}]},'evidence':[{'shot_id':'S1','video_path':str(self.video),'source_sha256':sha256(self.video),'frames':[str(self.frame)]}]}
  self.save()
 def save(self):
  write_json(self.audit,self.record)
  write_json(self.res,{'audit_sha256':sha256(self.audit),'job_id':'job','shot_id':'S1','video_sha256':sha256(self.video),'reviewer':'codex_sampled_visual_review','disposition':'advisory_no_regeneration','reason':'Framing cut, no visible mutation in denser samples','limitations':['sampled only'],'content_approved':False,'evidence_files':[{'path':str(self.frame),'sha256':sha256(self.frame)}],'original_findings':self.record['review']['shots'][0]['findings'],'no_visible_material_defect':True})
  write_json(self.audit.with_suffix('.resolutions.json'),{'items':[{'path':str(self.res),'sha256':sha256(self.res)}]})
 def test_resolution_does_not_mutate_original(self):
  before=self.audit.read_bytes();self.assertEqual(resolved_shots(self.audit),{'S1'});self.assertEqual(self.audit.read_bytes(),before)
 def test_material_rejection_or_failed_api_cannot_be_resolved(self):
  self.record['review']['shots'][0]['verdict']='reject';self.save();self.assertEqual(resolved_shots(self.audit),set())
  self.record['review']['shots'][0]['verdict']='review';self.record['review']['shots'][0]['findings'][0]['severity']='high';self.save();self.assertEqual(resolved_shots(self.audit),set())
  self.record['status']='audit_failed';self.save();self.assertEqual(resolved_shots(self.audit),set())
 def test_changed_media_and_evidence_rejected(self):
  self.video.write_bytes(b'changed')
  with self.assertRaises(ValueError):resolved_shots(self.audit)
 def test_changed_frame_rejected(self):
  self.frame.write_bytes(b'changed')
  with self.assertRaises(ValueError):resolved_shots(self.audit)
 def test_changed_resolution_rejected(self):
  self.res.write_text('{}')
  with self.assertRaises(ValueError):resolved_shots(self.audit)
