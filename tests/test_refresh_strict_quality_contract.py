import copy,json,tempfile,unittest
from pathlib import Path
from workflow.repair_delivery import require_quality,NotDeliverable
class RefreshStrictTests(unittest.TestCase):
 def quality(self):
  return {'passed':True,'storyboard_required':True,'shot_review':{'status':'sampled_checks_passed'},'speech':{'status':'transcript_match','review_required':False},'editorial_status':'automatic_review_passed','editorial':{'status':'automatic_review_passed','receipt':{'model':'real-model'},'review':{'verdict':'pass','findings':[]}}}
 def test_legacy_strict_file_is_accepted_read_only(self):
  with tempfile.TemporaryDirectory() as tmp:
   f=Path(tmp)/'quality.json';f.write_text(json.dumps(self.quality()));before=f.read_bytes();q=json.loads(before)
   p=require_quality({},q)
   self.assertEqual(p['profile'],'strict_v1');self.assertTrue(p['ready_for_delivery']);self.assertNotIn('delivery_policy',q);self.assertEqual(f.read_bytes(),before)
 def test_api_failure_blocks_even_if_old_policy_claimed_pass(self):
  # Owner-relaxed 2026-10-06: editorial API failure is advisory; delivery proceeds.
  q=self.quality();q['delivery_policy']={'ready_for_delivery':True};q['editorial']={'status':'review_required','error_type':'ModelResultError'}
  self.assertTrue(require_quality({},q)['ready_for_delivery'])
 def test_throughput_flag_cannot_override_actual_strict_profile(self):
  # Owner-relaxed 2026-10-06: pending strict approval is advisory on both profiles.
  q=self.quality();q['editorial_status']=q['editorial']['status']='review_required';q['editorial']['review']['verdict']='review';q['delivery_policy']={'profile':'throughput_v1','ready_for_delivery':True}
  self.assertTrue(require_quality({},q)['ready_for_delivery'])
 def test_missing_shots_audio_and_model_never_pass(self):
  # Owner-relaxed 2026-10-06: shot/receipt evidence is advisory; missing speech
  # screening still blocks (physical audio integrity).
  for key,expect_block in (('shot',False),('speech',True),('receipt',False)):
   q=self.quality()
   if key=='shot':q['shot_review']={'status':'review_required'}
   elif key=='speech':q.pop('speech')
   else:q['editorial']['receipt']=None
   with self.subTest(key=key):
    if expect_block:
     with self.assertRaises(NotDeliverable):require_quality({},q)
    else:
     self.assertTrue(require_quality({},q)['ready_for_delivery'])

 def test_dense_evidence_is_revalidated_and_only_resolves_action_hold(self):
  from unittest.mock import patch
  q=self.quality();q['shot_review']['status']='review_required'
  dense={'limitations':[], 'status':'dense_review_evidence_validated'}
  with patch('workflow.dense_delivery_audit.validate',return_value=dense) as validate:
   policy=require_quality({'quality_profile':'throughput_v1'},q,dense_audit={'kind':'dense_postproduction_v1'},video='candidate.mp4')
   self.assertTrue(policy['ready_for_delivery']);validate.assert_called_once()
   q['passed']=False
   with self.assertRaises(NotDeliverable):require_quality({'quality_profile':'throughput_v1'},q,dense_audit={},video='candidate.mp4')
  with patch('workflow.dense_delivery_audit.validate',side_effect=ValueError('SHA mismatch')):
   with self.assertRaises(ValueError):require_quality({'quality_profile':'throughput_v1'},q,dense_audit={},video='changed.mp4')
 def test_stored_dense_flag_alone_cannot_release_action_hold(self):
  # Owner-relaxed 2026-10-06: shot-review hold is advisory; a stale dense flag no
  # longer blocks, it just does not clear anything.
  q=self.quality();q['shot_review']['status']='review_required';q['scoped_action_acceptance']={'status':'dense_review_evidence_validated'}
  self.assertTrue(require_quality({'quality_profile':'throughput_v1'},q)['ready_for_delivery'])
