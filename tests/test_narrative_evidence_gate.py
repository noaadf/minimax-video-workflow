import json,unittest
from pathlib import Path
from workflow.narrative_gate import validate_contract
from workflow.narrative_evidence import validate
ROOT=Path(__file__).resolve().parents[1]
class GateTests(unittest.TestCase):
 def plan(self):
  return json.loads((ROOT/'research/deadline30_20261002/tricycle_story_r2/story_manifest_r2_ready.json').read_text())
 def test_existing_manifest_compatible(self):validate_contract(self.plan())
 def test_observed_contradiction_blocks(self):
  p=self.plan();p['narrative_evidence']=json.loads((ROOT/'research/tutorial_workflow/r2_sidecar.json').read_text())
  with self.assertRaisesRegex(ValueError,'OBSERVED_MUTATION'):validate_contract(p)
 def test_wrong_shots_rejected(self):
  p=self.plan();p['narrative_evidence']={'shots':[]}
  with self.assertRaisesRegex(ValueError,'cover executable'):validate_contract(p)
