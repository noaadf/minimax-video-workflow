import json,tempfile,unittest
from pathlib import Path
from workflow.plan import sha256
from workflow.sampled_supplement import resolved_shots,REQUIRED
class SupplementTests(unittest.TestCase):
 def test_binding_and_no_negative_override(self):
  with tempfile.TemporaryDirectory() as d:
   b=Path(d);v=b/'v';v.write_bytes(b'video');f=b/'f';f.write_bytes(b'frame');a=b/'audit.json';s=b/'supp.json'
   audit={'job_id':'r3','review':{'shots':[{'shot_id':'S07','verdict':'pass','findings':[]}]},'evidence':[{'shot_id':'S07','video_path':str(v),'source_sha256':sha256(v),'frames':[str(f)],'sample_seconds':[0]}]}
   a.write_text(json.dumps(audit));sup={'shot_id':'S07','job_id':'r3','audit_sha256':sha256(a),'video_sha256':sha256(v),'verdict':'pass','checks':{k:True for k in REQUIRED},'frames':[{'path':str(f),'sha256':sha256(f),'time_seconds':0}]}
   def save():
    s.write_text(json.dumps(sup));a.with_suffix('.supplements.json').write_text(json.dumps({'items':[{'path':str(s),'sha256':sha256(s)}]}))
   save();self.assertEqual(resolved_shots(a),{'S07'})
   sup['job_id']='r2';save()
   with self.assertRaises(ValueError):resolved_shots(a)
   sup['job_id']='r3';save();v.write_bytes(b'changed')
   with self.assertRaises(ValueError):resolved_shots(a)
   v.write_bytes(b'video');audit['review']['shots'][0]['findings']=[{'severity':'high'}];a.write_text(json.dumps(audit));sup['audit_sha256']=sha256(a);save();self.assertEqual(resolved_shots(a),set())
