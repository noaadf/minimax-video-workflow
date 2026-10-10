import json,tempfile,unittest
from pathlib import Path
from workflow.repair_delivery import require_binding,NotDeliverable,refresh,copy_delivery
from workflow.plan import sha256,write_json
class RepairDelivery(unittest.TestCase):
 def binding(self):
  return ({'state':'completed','request_id':'r','job_id':'old','new_job_id':'new','product_id':'root'}, {'id':'r','job_id':'old','status':'completed'}, {'id':'new','root_job_id':'root','parent_job_id':'old','user_repair_request_id':'r'})
 def test_exact_binding(self):
  i,r,p=self.binding();require_binding(i,r,p,'new')
 def test_old_or_other_version_cannot_complete(self):
  i,r,p=self.binding()
  with self.assertRaises(NotDeliverable):require_binding(i,r,p,'old')
  r['job_id']='other'
  with self.assertRaises(NotDeliverable):require_binding(i,r,p,'new')
 def test_request_pending_cannot_deliver(self):
  i,r,p=self.binding();r['status']='pending'
  with self.assertRaises(NotDeliverable):require_binding(i,r,p,'new')
 def test_manifest_wrong_root_rejected(self):
  i,r,p=self.binding();p['root_job_id']='other'
  with self.assertRaises(NotDeliverable):require_binding(i,r,p,'new')
 def test_pending_is_zero_despite_old_delivery(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);folder=root/'output/production/user_repair30_20261002';write_json(folder/'driver_status.json',{'items':{'r':{'state':'rendering','product_id':'p'}}});write_json(root/'exports/deadline30_20261002/index.json',{'count':30,'items':[{'system_ready':True}]});r=refresh(root);self.assertEqual(r['count'],0);self.assertTrue((root/'exports/user_repair30_20261002/index.json').exists())
 def test_verified_copy_idempotent_and_checksums(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);dest=root/'exports';dest.mkdir();sources={}
   for name in ('video.mp4','cover.png','publication.txt'):
    p=root/name;p.write_bytes(name.encode());sources[name]=str(p)
   verified={'product_id':'p','version_job_id':'new','video_sha256':sha256(sources['video.mp4']),'sources':sources,'evidence':{}}
   one=copy_delivery(verified,dest);two=copy_delivery(verified,dest);self.assertEqual(one,two)
   self.assertTrue(all(sha256(v['path'])==v['sha256'] for v in two['files'].values()))
