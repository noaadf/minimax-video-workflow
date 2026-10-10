import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts import run_user_repair30 as driver
from workflow.narrative_gate import review_manifest,CHECKS
from workflow.plan import write_json,sha256
from tests.test_narrative_gate import NarrativeTests
class DynamicApprovals(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.mock=patch.object(driver,'OUT',self.root);self.mock.start();self.addCleanup(self.mock.stop)
 def entry(self,key):
  p=self.root/(key+'.json');r=self.root/(key+'.review.json');write_json(p,NarrativeTests().plan())
  review_manifest(p,r,lambda *a,**k:({'verdict':'pass','checks':{c:True for c in CHECKS},'issues':[]},{'model':'unit-test','usage':{'output_tokens':1}}))
  return {'product_id':key,'status':'approved','manifest_path':str(p),'sha256':sha256(p),'review_evidence_path':str(r),'review_evidence_sha256':sha256(r),'checks':{c:True for c in CHECKS}}
 def index(self,items):write_json(self.root/'narrative_approvals.json',{'status':'partial','items':items})
 def test_missing_index_yields_no_permission(self):self.assertEqual(driver.narrative_overrides(),{})
 def test_single_then_dynamic_addition(self):
  a=self.entry('tricycle');self.index([a]);self.assertEqual(set(driver.narrative_overrides()),{'tricycle'})
  b=self.entry('next');self.index([a,b]);self.assertEqual(set(driver.narrative_overrides()),{'tricycle','next'})
 def test_bad_product_does_not_release_or_block_valid_product(self):
  a=self.entry('good');b=self.entry('bad');b['sha256']='bad';self.index([a,b]);self.assertEqual(set(driver.narrative_overrides()),{'good'});self.assertIn('bad',driver.narrative_overrides.last_errors)
 def test_reviewed_boolean_is_insufficient(self):
  self.index([{'product_id':'x','status':'approved','reviewed':True}]);self.assertEqual(driver.narrative_overrides(),{})
