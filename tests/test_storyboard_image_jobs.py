import json,tempfile,unittest
from pathlib import Path
from PIL import Image
from workflow.storyboard_image_jobs import ImageJobs
class ImageJobTests(unittest.TestCase):
 def test_dependency_claim_replay_and_real_media(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);m=root/'manifest.json';m.write_text(json.dumps({'shots':[{'id':'S1','prompt':'scene','storyboard':{'state_before':{'x':0},'state_after':{'x':1}}}]}));jobs=ImageJobs(root/'jobs');jobs.add(m,'p');jobs.add(m,'p');self.assertEqual(len(list((root/'jobs').glob('*.json'))),2)
   first=jobs.claim();self.assertEqual(first['role'],'start');self.assertIsNone(jobs.claim())
   with self.assertRaises(Exception):jobs.complete(first['id'],first['claim_token'],m)
   im=root/'image.png';Image.new('RGB',(36,64)).save(im)
   done=jobs.complete(first['id'],first['claim_token'],im);self.assertEqual(done,jobs.complete(first['id'],first['claim_token'],im));second=jobs.claim();self.assertEqual(second['role'],'end');self.assertEqual(second['reference_images'],[done['image_path']]);self.assertIsNone(jobs.claim())
   with self.assertRaises(ValueError):jobs.complete(second['id'],'wrong',im)

 def test_same_scene_start_waits_and_uses_reference(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);m=root/'manifest.json';shots=[{'id':sid,'prompt':'scene','storyboard':{'scene_id':'same','state_before':{'x':0},'state_after':{'x':1}}} for sid in ['S1','S2']];m.write_text(json.dumps({'shots':shots}));jobs=ImageJobs(root/'jobs');jobs.add(m,'p')
   first=jobs.claim('p');self.assertEqual(first['shot_id'],'S1');self.assertIsNone(jobs.claim('p'))
   im=root/'image.png';Image.new('RGB',(36,64)).save(im);done=jobs.complete(first['id'],first['claim_token'],im)
   end=jobs.claim('p');self.assertEqual(end['role'],'end');self.assertIsNone(jobs.claim('p'));jobs.complete(end['id'],end['claim_token'],im);nextshot=jobs.claim('p');self.assertEqual(nextshot['shot_id'],'S2');self.assertEqual(nextshot['reference_images'],[str((root/'jobs/assets'/(end['id']+'.png')).resolve())]);self.assertEqual(nextshot['reference_role'],'same scene continuity')

 def test_one_repair_preserves_original_and_exhausts_budget(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);m=root/'manifest.json';m.write_text(json.dumps({'shots':[{'id':'S1','prompt':'scene','storyboard':{'state_before':{'x':0},'state_after':{'x':0}}}]}));jobs=ImageJobs(root/'jobs');jobs.add(m,'p');t=jobs.claim();im=root/'a.png';Image.new('RGB',(36,64),'red').save(im);old=jobs.complete(t['id'],t['claim_token'],im);jobs.reject(t['id'],'wrong camera');jobs.repair(t['id'],'camera too high','ground level')
   with self.assertRaises(ValueError):jobs.repair(t['id'],'again','again')
   t=jobs.claim();Image.new('RGB',(36,64),'blue').save(im);new=jobs.complete(t['id'],t['claim_token'],im);self.assertNotEqual(old['image_path'],new['image_path']);self.assertTrue(Path(old['image_path']).exists());self.assertEqual(jobs.reject(t['id'],'still wrong')['state'],'isolated');self.assertIsNone(jobs.claim())

 def test_claim_derives_canvas_from_manifest_including_legacy_tasks(self):
  for extra,ratio,dimensions in [({},'9:16',{'width':1080,'height':1920}),
      ({'aspect_ratio':'9:16'},'9:16',{'width':1080,'height':1920}),
      ({'aspect_ratio':'16:9'},'16:9',{'width':1920,'height':1080}),
      ({'aspect_ratio':'1:1','target_dimensions':{'width':1024,'height':1024}},'1:1',{'width':1024,'height':1024}),
      ({'aspect_ratio':'9:16','width':720,'height':1280},'9:16',{'width':720,'height':1280})]:
   with self.subTest(extra=extra),tempfile.TemporaryDirectory() as temp:
    root=Path(temp);m=root/'manifest.json'
    m.write_text(json.dumps({**extra,'shots':[{'id':'S1','prompt':'scene','storyboard':{'state_before':{},'state_after':{}}}]}))
    jobs=ImageJobs(root/'jobs');jobs.add(m,'p')
    raw=json.loads(next((root/'jobs').glob('*.json')).read_text())
    self.assertNotIn('target_dimensions',raw) # canvas is derived at claim time
    self.assertEqual(raw['image_generation_policy'],'documentary_natural_v1')
    self.assertIn('自然中性光',raw['prompt']);self.assertIn('禁止电影调色',raw['prompt'])
    claim=jobs.claim();self.assertEqual(claim['aspect_ratio'],ratio);self.assertEqual(claim['target_dimensions'],dimensions)
    self.assertEqual(claim['id'],raw['id'])

 def test_bad_canvas_is_isolated_and_does_not_block_other_product(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);jobs=ImageJobs(root/'jobs')
   for product,extra in [('a',{'aspect_ratio':'9:16','target_dimensions':{'width':1920,'height':1080}}),('b',{'aspect_ratio':'9:16'})]:
    m=root/(product+'.json');m.write_text(json.dumps({**extra,'shots':[{'id':'S1','prompt':'scene','storyboard':{'state_before':{},'state_after':{}}}]}));jobs.add(m,product)
   claim=jobs.claim();self.assertEqual(claim['product_id'],'b')
   bad=next(json.loads(p.read_text()) for p in (root/'jobs').glob('*.json') if json.loads(p.read_text())['product_id']=='a')
   self.assertEqual(bad['state'],'isolated');self.assertEqual(bad['reason'],'invalid_manifest_canvas')

 def test_new_schema_wrong_ratio_preserves_evidence_and_requires_repair(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);m=root/'manifest.json';m.write_text(json.dumps({'aspect_ratio':'9:16','shots':[{'id':'S1','prompt':'scene','storyboard':{'state_before':{},'state_after':{}}}]}));jobs=ImageJobs(root/'jobs');jobs.add(m,'p')
   t=jobs.claim();self.assertEqual(t['image_job_schema_version'],2)
   image=root/'landscape.png';Image.new('RGB',(160,90)).save(image)
   failed=jobs.complete(t['id'],t['claim_token'],image)
   self.assertEqual(failed['state'],'needs_diagnosed_repair');self.assertFalse(failed['approval']);self.assertEqual(failed['canvas_check']['status'],'failed')
   self.assertTrue(Path(failed['image_path']).is_file());self.assertTrue(image.is_file())
   self.assertEqual(failed,jobs.complete(t['id'],t['claim_token'],image))
   jobs.repair(t['id'],failed['diagnosis'],'Regenerate whole scene vertically at 9:16')
   repaired=jobs.claim();isolated=jobs.complete(repaired['id'],repaired['claim_token'],image)
   self.assertEqual(isolated['state'],'isolated');self.assertTrue(Path(failed['image_path']).is_file());self.assertNotEqual(isolated['image_path'],failed['image_path'])
   self.assertIsNone(jobs.claim())

 def test_versionless_legacy_task_does_not_gain_strict_canvas_on_claim(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);m=root/'manifest.json';m.write_text(json.dumps({'aspect_ratio':'9:16','shots':[{'id':'S1','prompt':'scene','storyboard':{'state_before':{},'state_after':{}}}]}));jobs=ImageJobs(root/'jobs');jobs.add(m,'p')
   path=next((root/'jobs').glob('*.json'));old=json.loads(path.read_text());old.pop('image_job_schema_version');path.write_text(json.dumps(old))
   jobs.add(m,'p');t=jobs.claim();self.assertNotIn('image_job_schema_version',t)
   image=root/'old_landscape.png';Image.new('RGB',(160,90)).save(image)
   self.assertEqual(jobs.complete(t['id'],t['claim_token'],image)['state'],'completed')

 def test_pixel_quantization_policy_and_audited_reconciliation(self):
  from workflow.storyboard_image_jobs import _canvas_check
  canvas={'aspect_ratio':'9:16','target_dimensions':{'width':1080,'height':1920}}
  self.assertEqual(_canvas_check(canvas,{'width':941,'height':1672})['status'],'passed')
  for width,height in [(1672,941),(1024,1536),(943,1672)]:
   self.assertEqual(_canvas_check(canvas,{'width':width,'height':height})['status'],'failed')
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);m=root/'manifest.json';m.write_text(json.dumps({'aspect_ratio':'9:16','shots':[{'id':'S1','prompt':'scene','storyboard':{'state_before':{},'state_after':{}}}]}));jobs=ImageJobs(root/'jobs');jobs.add(m,'p');t=jobs.claim()
   image=root/'rounded.png';Image.new('RGB',(941,1672)).save(image);r=jobs.complete(t['id'],t['claim_token'],image);self.assertEqual(r['state'],'completed')
   r.update(state='needs_diagnosed_repair',approval=False,diagnosis='Image canvas rounding',rejection_reason='Image canvas rounding');r['canvas_check'].update(rule='exact_aspect_ratio_v1',status='failed')
   path=root/'jobs'/(t['id']+'.json');path.write_text(json.dumps(r))
   fixed=jobs.reconcile_canvas_quantization(t['id']);self.assertEqual(fixed['state'],'completed');self.assertNotIn('repair_attempt',fixed)
   history=json.loads(Path(fixed['canvas_policy_reconciliation']['prior_evidence_path']).read_text());self.assertEqual(history['diagnosis'],'Image canvas rounding')
   self.assertEqual(fixed,jobs.reconcile_canvas_quantization(t['id']))
