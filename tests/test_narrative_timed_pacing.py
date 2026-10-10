import unittest
from workflow.plan import validate
class NarrativePacingTests(unittest.TestCase):
 def plan(self,durations,policy=None):
  p={'schema_version':1,'id':'pacing_test','template':'engineering_tension','duration_seconds':sum(durations),'aspect_ratio':'9:16','facts':[{'id':'F1','text':'source fact','source_url':'https://example.org/source'}],'continuity_bible':'one subject','comment_prompt':'Your choice?','cover_title':'Test','music':{'path':'music.wav','license_note':'original'},'narration':[{'start':0,'end':2,'text':'A fact','fact_ids':['F1']}],'shots':[{'id':f'S{i}','camera':f'view{i}','duration_seconds':v,'duration_reason':'Allow the subject to complete the movement while narration explains the result.','prompt':'One subject completing an action.','fact_ids':['F1']} for i,v in enumerate(durations)]}
  if policy is not None:p['pacing_policy']=policy
  return p
 def test_long_first_shot_and_fifteen_second_action_supported(self):
  validate(self.plan([15,12,10,8,5,4],'narrative_timed_v1'))
 def test_all_shots_require_explanation_including_short_ones(self):
  for value in (None,'','  ',42):
   p=self.plan([8,4,4,4,4,4],'narrative_timed_v1');p['shots'][-1]['duration_reason']=value
   with self.subTest(value=value),self.assertRaisesRegex(ValueError,'duration_reason'):validate(p)
 def test_more_than_fifteen_rejected(self):
  with self.assertRaisesRegex(ValueError,'1–15'):validate(self.plan([16,4,4,4,4,4],'narrative_timed_v1'))
 def test_legacy_first_and_later_limits_unchanged(self):
  p=self.plan([3,5,5,4,4,4]);[s.pop('duration_reason') for s in p['shots']];validate(p)
  with self.assertRaisesRegex(ValueError,'first hook'):validate(self.plan([4,4,4,4,4,4]))
  with self.assertRaisesRegex(ValueError,'1–5'):validate(self.plan([3,6,4,4,4,4]))
 def test_unknown_policy_rejected(self):
  with self.assertRaisesRegex(ValueError,'unknown pacing_policy'):validate(self.plan([3,4,4,4,4,4],'anything'))
