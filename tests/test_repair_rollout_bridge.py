import hashlib,json,tempfile,unittest
from pathlib import Path
from workflow.repair_rollout_bridge import adopt
from workflow.queue import Queue

class BridgeTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.q=Queue(self.root/'queue.sqlite');self.registered=[]
  with self.q.connect() as c:c.execute('CREATE TABLE production_items(item_id TEXT PRIMARY KEY,current_job TEXT)')
 def add(self,n,folder_name=None,jid=None,valid=True):
  jid=jid or 'new_'+n;folder=self.root/'storyboard_rollout/requests'/(folder_name or n);folder.mkdir(parents=True)
  film=folder/'film.mp4';film.write_bytes(b'export')
  manifest=folder/'manifest.json';manifest.write_text(json.dumps({'id':jid,'root_job_id':n,'user_repair_request_id':'r_'+n}))
  request={'state':'completed','job_id':jid,'manifest_path':str(manifest),'film_path':str(film),'film_sha256':hashlib.sha256(b'export' if valid else b'changed').hexdigest()}
  (folder/'deployment_request.json').write_text(json.dumps(request))
  if not any(r['product_id']==n for r in self.registered):self.registered.append({'product_id':n,'request_id':'r_'+n,'job_id':'old_'+n})
  with self.q.connect() as c:c.execute('INSERT OR REPLACE INTO production_items VALUES(?,?)',(n,jid))
  return folder
 def run_adopt(self,items):return adopt(self.root,items,self.registered,self.q)
 def test_failed_peer_and_replay_preserve_audit(self):
  self.add('bad',valid=False);self.add('good');items={}
  self.assertEqual(self.run_adopt(items),['r_good']);items['r_good']['state']='auditing'
  self.assertEqual(self.run_adopt(items),[]);self.assertEqual(items['r_good']['state'],'auditing')
  self.assertEqual(items['r_good']['job_id'],'old_good');self.assertNotIn('r_bad',items)
 def test_bad_json_and_missing_manifest_do_not_starve_good_peer(self):
  bad=self.add('bad');(bad/'deployment_request.json').write_text('{broken')
  missing=self.add('missing');(missing/'manifest.json').unlink();self.add('zgood');items={}
  self.assertEqual(self.run_adopt(items),['r_zgood'])
  self.assertTrue((bad/'repair_bridge_error.json').exists());self.assertTrue((missing/'repair_bridge_error.json').exists())
 def test_historical_rollouts_never_reset_current_audit(self):
  self.add('p',folder_name='z-old',jid='old_revision');self.add('p',folder_name='a-current',jid='new_revision');items={}
  self.assertEqual(self.run_adopt(items),['r_p']);items['r_p']['state']='auditing'
  for _ in range(3):self.assertEqual(self.run_adopt(items),[])
  self.assertEqual(items['r_p']['new_job_id'],'new_revision');self.assertEqual(items['r_p']['state'],'auditing')
 def test_changed_card_does_not_replace_active_audit(self):
  self.add('p',jid='new_revision');items={'r_p':{'state':'auditing','new_job_id':'inflight_old'}}
  self.assertEqual(self.run_adopt(items),[]);self.assertEqual(items['r_p']['new_job_id'],'inflight_old')
 def test_wrong_request_identity_isolated(self):
  folder=self.add('p');p=folder/'manifest.json';doc=json.loads(p.read_text());doc['user_repair_request_id']='unrelated';p.write_text(json.dumps(doc));items={}
  self.assertEqual(self.run_adopt(items),[]);self.assertTrue((folder/'repair_bridge_error.json').exists())
