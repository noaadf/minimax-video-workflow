import unittest
import tempfile
from pathlib import Path
from scripts.run_user_repair30 import audit_decision,OVERRIDES
from workflow.plan import sha256
class UserRepairAuditGate(unittest.TestCase):
 def record(self):return {'finished_at':1,'status':'sampled_checks_passed','review':{'shots':[{'shot_id':'s1','verdict':'pass','confidence':.9,'findings':[]}]}}
 def test_full_review_pass(self):self.assertEqual(audit_decision(self.record(),['s1']),[])
 def test_no_partial_or_failed_release(self):
  r=self.record();r.pop('finished_at');self.assertIsNone(audit_decision(r,['s1']))
  r=self.record();r['status']='audit_failed';self.assertIsNone(audit_decision(r,['s1']))
  self.assertIsNone(audit_decision(self.record(),['s1','s2']))
 def test_major_gibberish_blocked(self):
  r=self.record();r['review']['shots'][0]['findings']=[{'severity':'medium','category':'generated_text'}];self.assertEqual(audit_decision(r,['s1']),['s1'])
 def test_uncertainty_not_pass(self):
  r=self.record();r['review']['shots'][0]['confidence']=.6;self.assertEqual(audit_decision(r,['s1']),['s1'])
 def test_no_static_override_bypasses_narrative_gate(self):
  self.assertEqual(OVERRIDES,{})
 def test_bound_minor_review_resolves_initial_flag_but_not_known_failure(self):
  with tempfile.TemporaryDirectory() as d:
   video=Path(d)/'video';video.write_bytes(b'fixture media')
   sheet=Path(d)/'sheet';sheet.write_bytes(b'fixture evidence')
   r=self.record();row=r['review']['shots'][0];row.update(verdict='review',confidence=.6,findings=[{'severity':'medium'}])
   r['evidence']=[{'shot_id':'s1','video_path':str(video),'source_sha256':sha256(video)}]
   r['exceptions']=[{'shot_id':'s1','review_execution_status':'completed','binding':{'video_sha256':sha256(video)},'evidence_sheet':str(sheet),'evidence_sha256':sha256(sheet),'review':{'checks':[{'criterion':c,'verdict':'pass','confidence':.95} for c in ('structure','scene_contents','narration_alignment')],'findings':[{'severity':'low'}]}}]
   self.assertEqual(audit_decision(r,['s1']),[])
   row['findings'].append({'origin':'sha_bound_known_material_failure'})
   self.assertEqual(audit_decision(r,['s1']),['s1'])
   row['findings'].pop();video.write_bytes(b'changed media')
   self.assertEqual(audit_decision(r,['s1']),['s1'])
