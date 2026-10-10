import unittest
from workflow.runner import visual_generation_request
class VisualRequestTests(unittest.TestCase):
 def setUp(self):
  self.shot={'id':'S01','prompt':'ONE red cargo tricycle, open saddle, two wooden crates.','duration_seconds':3.5,'seed':42}
  self.cfg={'remote_root':'/worker','generation':{'steps':50}}
 def test_mixed_story_and_audio_excluded_for_all_profiles(self):
  for profile in ('throughput_v1','legacy',None):
   p={'quality_profile':profile,'continuity_bible':'台词：这是我们的故事；后期字幕/配乐/一车货—维修顾虑','narration':[{'text':'旁白内容'}],'backend':self.cfg}
   _,r=visual_generation_request(p,self.shot,{'id':'j:S01'},self.cfg)
   self.assertEqual(r['prompt'],self.shot['prompt']);self.assertEqual(r['duration_seconds'],4);self.assertEqual(r['seed'],42)
 def test_explicit_visual_field_only_and_changes_cache_identity(self):
  a,r=visual_generation_request({},self.shot,{'id':'j:S01'},self.cfg)
  b,s=visual_generation_request({'visual_continuity':'Same open red cargo bed.','continuity_bible':'DO NOT SEND'},self.shot,{'id':'j:S01'},self.cfg)
  self.assertNotEqual(a,b);self.assertEqual(s['prompt'],'Same open red cargo bed.\n'+self.shot['prompt']);self.assertNotIn('DO NOT SEND',s['prompt'])
 def test_invalid_visual_field_fails_closed(self):
  with self.assertRaises(ValueError):visual_generation_request({'visual_continuity':{'story':'bad'}},self.shot,{'id':'x'},self.cfg)
