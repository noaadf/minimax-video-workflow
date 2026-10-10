import unittest
from workflow.audit_disposition import disposition
class DispositionTests(unittest.TestCase):
 def test_failed_api_never_passes(self):
  self.assertEqual(disposition({'status':'audit_failed','finished_at':1})['state'],'incomplete_audit')
 def test_confidence_only_does_not_trigger_regeneration(self):
  r=disposition({'finished_at':1,'review':{'shots':[{'shot_id':'S07','verdict':'pass','confidence':.8,'findings':[]}]}})
  self.assertEqual(r['shots'][0]['disposition'],'uncertainty_only_no_regeneration');self.assertFalse(r['delivery_approved'])
 def test_known_failure_remains_blocking(self):
  r=disposition({'finished_at':1,'review':{'shots':[{'shot_id':'S03','verdict':'pass','findings':[{'origin':'sha_bound_known_material_failure'}]}]}})
  self.assertEqual(r['shots'][0]['disposition'],'material_repair_required')
 def test_unbound_detailed_review_not_trusted(self):
  r=disposition({'finished_at':1,'review':{'shots':[{'shot_id':'S01','verdict':'review'}]},'exceptions':[{'shot_id':'S01','review_execution_status':'completed'}]})
  self.assertEqual(r['shots'][0]['disposition'],'review_required')
