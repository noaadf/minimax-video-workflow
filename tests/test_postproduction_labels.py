import tempfile,unittest
from pathlib import Path
from workflow.media import captions
class PostproductionLabelTests(unittest.TestCase):
 def plan(self):
  return {'duration_seconds':15,'cover_title':'封面','narration':[],'shots':[{'id':'S01','duration_seconds':4.25},{'id':'S06','duration_seconds':6.5},{'id':'S07','duration_seconds':4.25}],'postproduction_labels':[{'shot_id':'S06','text':'售后布局示意','generated_in_model':False}]}
 def render(self,p,cover=False):
  with tempfile.TemporaryDirectory() as d:
   f=Path(d)/'test.ass';captions(p,f,cover=cover);return f.read_text()
 def test_actual_ass_cumulative_timing_and_separate_position(self):
  s=self.render(self.plan());self.assertIn('Dialogue: 1,0:00:04.25,0:00:10.75,ShotLabel,,0,0,0,,售后布局示意',s)
  styles={line.split(',')[0]:line.split(',') for line in s.splitlines() if line.startswith('Style:')}
  self.assertEqual(styles['Style: ShotLabel'][-5:-1],['7','48','145','165'])
  self.assertEqual(styles['Style: Label'][-2],'65');self.assertEqual(styles['Style: Main'][-5],'2');self.assertIn('AI演绎 · 非现场实拍',s)
 def test_cover_has_no_local_label_dialogue(self):
  s=self.render(self.plan(),True);self.assertNotIn(',,售后布局示意',s);self.assertNotIn(',ShotLabel,,',s)
 def test_illegal_shot_or_empty_text_rejected(self):
  for change in ({'shot_id':'missing'},{'text':'  '},{'text':None},{'generated_in_model':True}):
   p=self.plan();p['postproduction_labels'][0].update(change)
   with self.subTest(change=change),self.assertRaises(ValueError):self.render(p)
 def test_ass_controls_and_newlines_cannot_inject_events(self):
  p=self.plan();p['postproduction_labels'][0]['text']='说明{\\pos(1,1)}\\N\nDialogue: forged'
  s=self.render(p);line=next(x for x in s.splitlines() if x.startswith('Dialogue:') and ',ShotLabel,' in x)
  self.assertNotIn('{',line);self.assertNotIn('\\',line);self.assertNotIn('\n',line);self.assertEqual(sum(',ShotLabel,,' in x for x in s.splitlines()),1)
