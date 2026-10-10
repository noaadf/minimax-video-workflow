import copy,json,tempfile,unittest
from pathlib import Path
from workflow.narrative_gate import validate_contract,review_manifest,require_review,bind_runtime_copy,CHECKS
from workflow.plan import write_json
class NarrativeTests(unittest.TestCase):
 def plan(self):
  p={'facts':[{'id':'F1'}],'shots':[{'id':str(i)} for i in range(3)],'narration':[{'text':'独立的故事旁白'+str(i)} for i in range(3)]}
  p['narrative_contract']={'central_question':'为何要解决这个问题？','story_one_sentence':'困难促成选择，行动带来可见变化。','beats':[{'id':str(i),'causal_role':'因果推进','claim':'具体事实','fact_ids':['F1']} for i in range(3)],'shots':[{'id':str(i),'beat_id':str(i),'narration_index':i,'narration':p['narration'][i]['text'],'relation_to_previous':'承接之前的问题','visible_action':'一个明确动作','visual_constraint':'一个稳定主体','fact_ids':['F1']} for i in range(3)],'ending':{'result':'结果','tradeoff':'代价','comment_prompt':'你会如何选？'}};return p
 def test_invalid_production_manifest_never_calls_paid_reviewer(self):
  from unittest.mock import Mock
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'p.json';r=Path(t)/'review.json'
   plan=self.plan();plan.update(schema_version=1,id='valid-id',template='invalid-template')
   write_json(p,plan);client=Mock()
   with self.assertRaises(ValueError):review_manifest(p,r,client)
   client.assert_not_called();self.assertFalse(r.exists())
 def test_missing_links_and_wrong_narration_rejected(self):
  p=self.plan();validate_contract(p);p['narrative_contract']['shots'][0]['narration']='不匹配'
  with self.assertRaises(ValueError):validate_contract(p)
 def test_exact_hash_and_receipt(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'p.json';r=Path(t)/'review.json';write_json(p,self.plan())
   def model(*args,**kw):return {'verdict':'pass','checks':{k:True for k in CHECKS},'issues':[]},{'model':'test','usage':{'output_tokens':100}}
   review_manifest(p,r,model);require_review(p,r)
   changed=self.plan();changed['narration'][0]['text']='不同';write_json(p,changed)
   with self.assertRaises(ValueError):require_review(p,r)
 def test_no_boolean_shortcut(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'p.json';r=Path(t)/'r.json';write_json(p,self.plan());write_json(r,{'reviewed':True})
   with self.assertRaises(ValueError):require_review(p,r)
 def test_runtime_cannot_inject_unreviewed_visual_continuity(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'p.json';r=Path(t)/'r.json';runtime=Path(t)/'runtime.json';write_json(p,self.plan())
   def model(*args,**kw):return {'verdict':'pass','checks':{k:True for k in CHECKS},'issues':[]},{'model':'test','usage':{'output_tokens':100}}
   review_manifest(p,r,model);changed=self.plan();changed['visual_continuity']='An unreviewed extra subject';write_json(runtime,changed)
   with self.assertRaises(ValueError):bind_runtime_copy(p,r,runtime,Path(t)/'bound.json')
 def test_pacing_labels_and_duration_reason_cannot_change_at_runtime(self):
  for field in ('pacing_policy','postproduction_labels','duration_reason'):
   with self.subTest(field=field),tempfile.TemporaryDirectory() as t:
    p=Path(t)/'p.json';r=Path(t)/'r.json';runtime=Path(t)/'runtime.json';write_json(p,self.plan())
    def model(*args,**kw):return {'verdict':'pass','checks':{k:True for k in CHECKS},'issues':[]},{'model':'test','usage':{}}
    review_manifest(p,r,model);changed=self.plan()
    if field=='duration_reason':changed['shots'][0][field]='Different pacing rationale'
    else:changed[field]='changed'
    write_json(runtime,changed)
    with self.assertRaises(ValueError):bind_runtime_copy(p,r,runtime,Path(t)/'bound.json')
