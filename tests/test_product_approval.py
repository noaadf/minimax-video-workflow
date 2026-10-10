from tests.test_dashboard_actions import ActionTests
from workflow.product_approval import approve
from workflow.products import products
from workflow.dashboard import snapshot
from workflow.dashboard_actions import request_repair,ActionError,export_film
from workflow.queue import Queue
from workflow.plan import write_json
from pathlib import Path
class ApprovalTests(ActionTests):
 def ready(self):
  write_json(self.root/'batch/throughput_policy_acceptance.json',{'items':[{'video_sha256':self.sha,'ready_for_delivery':True,'blockers':[]}]})
 def test_no_approval_for_merely_existing_file(self):
  with self.assertRaises(ActionError):approve(self.root,'film',self.sha)
 def test_approve_locks_family_and_export_version(self):
  self.ready();approve(self.root,'film',self.sha)
  self.assertTrue(products(self.root,[]) [0]['approved'])
  with self.assertRaises(ActionError):request_repair(self.root,'film',self.sha,'change')
  with self.assertRaises(ValueError):Queue(self.root/'queue.sqlite').submit('film-repair','digest',{'id':'film-repair','parent_job_id':'film'},[])
  (self.f/'video.mp4').write_bytes(b'mutated')
  with self.assertRaises(ActionError):export_film(self.root,'film',self.sha,str(Path(self.tmp.name)/'export'))
 def test_pending_repair_prevents_signoff(self):
  self.ready();request_repair(self.root,'film',self.sha,'change')
  with self.assertRaises(ActionError):approve(self.root,'film',self.sha)
 def test_claim_respects_lock(self):
  self.ready();approve(self.root,'film',self.sha)
  q=Queue(self.root/'queue.sqlite')
  with q.connect() as c:
   c.execute('INSERT INTO jobs VALUES(?,?,?)',('child','d','{"parent_job_id":"film"}'))
   c.execute("INSERT INTO tasks(id,job_id,stage,capability,payload,deps) VALUES('child:t','child','shot','cpu','{}','[]')")
  self.assertIsNone(q.claim(['cpu']))
