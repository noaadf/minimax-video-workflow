import json,tempfile,unittest
from pathlib import Path
from scripts.validate_fl2va_chain import references,content_copy,verified_backend,CHECKS
from workflow.plan import sha256,write_json
class ChainTests(unittest.TestCase):
 def setup_files(self,d):
  p=Path(d);a={}
  for name in ['start','segment1_target','segment2_target','actual_last','first','middle','video']:
   f=p/(name+'.png');f.write_bytes(name.encode());a[name]={'path':str(f),'sha256':sha256(f)}
  return p,a
 def test_second_segment_requires_passed_first_and_actual_last(self):
  with tempfile.TemporaryDirectory() as d:
   p,a=self.setup_files(d);plan={'anchors':a}
   with self.assertRaises(FileNotFoundError):references(plan,p,2)
   s=p/'segment1';s.mkdir();frames={k:a['actual_last' if k=='last' else k] for k in ['first','middle','last']}
   write_json(s/'result.json',{'video_path':a['video']['path'],'video_sha256':a['video']['sha256'],'frames':frames})
   r={'verdict':'pass','reviewer':'test-reviewer','evidence':'inspected every sample','video_sha256':a['video']['sha256'],'checks':{k:True for k in CHECKS},'sample_sha256':{k:v['sha256'] for k,v in frames.items()}}
   write_json(s/'review.json',r);refs=references(plan,p,2);self.assertEqual(refs[0],str(Path(a['actual_last']['path']).resolve()));self.assertNotEqual(refs[0],a['segment1_target']['path'])
   r['checks']['middle_frame_valid']=False;write_json(s/'review.json',r)
   with self.assertRaises(ValueError):references(plan,p,2)
 def test_missing_middle_evidence_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p,a=self.setup_files(d);s=p/'segment1';s.mkdir();frames={'first':a['first'],'last':a['actual_last']};write_json(s/'result.json',{'video_path':a['video']['path'],'video_sha256':a['video']['sha256'],'frames':frames});write_json(s/'review.json',{'verdict':'pass','reviewer':'x','evidence':'x','video_sha256':a['video']['sha256'],'checks':{k:True for k in CHECKS},'sample_sha256':{k:v['sha256'] for k,v in frames.items()}})
   with self.assertRaises(ValueError):references({'anchors':a},p,2)
 def test_content_address_rejects_modified_asset(self):
  with tempfile.TemporaryDirectory() as d:
   p,a=self.setup_files(d);dest=Path(content_copy(a['start']['path'],a['start']['sha256'],p/'anchors'));self.assertEqual(dest.stem,a['start']['sha256']);dest.chmod(0o644);dest.write_bytes(b'changed')
   with self.assertRaises(ValueError):content_copy(a['start']['path'],a['start']['sha256'],p/'anchors')
 def test_flash_backend_disallowed(self):
  with self.assertRaises(ValueError):verified_backend('research/ascend_worker_backend_pool32_flashgen_slot0.json')
 def test_active_controller_lock_prevents_any_generation(self):
  import fcntl
  from unittest.mock import patch
  import scripts.validate_fl2va_chain as m
  with tempfile.TemporaryDirectory() as d:
   p,a=self.setup_files(d);out=p/'experiment';out.mkdir();b=p/'backend.json';b.write_text('{}');write_json(out/'prepared.json',{'backend_path':str(b),'backend_sha256':sha256(b)})
   folder=p/'output/production';folder.mkdir(parents=True)
   with (folder/'queue.sqlite.controller-service.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with patch.object(m,'ROOT',p),patch.object(m,'verified_backend',return_value={}),patch.object(m,'references',return_value=[]),patch.object(m,'ssh_generate') as generate:
     with self.assertRaises(BlockingIOError):m.run_segment(out,1)
     generate.assert_not_called()
 def test_prepare_refuses_missing_approved_keyframes(self):
  from unittest.mock import patch
  import scripts.validate_fl2va_chain as m
  from workflow.plan import digest
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);prompts=['One vehicle parked.','Same vehicle, short camera motion.'];spec=p/'spec.json';write_json(spec,{'backend_path':'unused','prompts':prompts,'prompt_review':{'verdict':'pass','reviewer':'reviewer','prompts_sha256':digest(prompts)},'anchors':{}})
   with patch.object(m,'verified_backend',return_value={}):
    with self.assertRaises(KeyError):m.prepare(spec,p/'out')
   self.assertFalse((p/'out/prepared.json').exists())
