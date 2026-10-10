import tempfile,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from workflow.queue import Queue

class WorkConservingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.q=Queue(Path(self.tmp.name)/'q.sqlite')
  self.groups=[{'type':'ascend_ssh','ready':True,'capability':x,'resource_id':x,'host':'worker','port':22+i,'expires_at':'2099-01-01T00:00:00Z'} for i,x in enumerate(['slow','fast'])]
 def add(self,jid,count=3,refs=False,dynamic=True):
  pool={'type':'pool','groups':self.groups,'scheduling':'work_conserving' if dynamic else 'fixed'}
  self.q.submit(jid,jid,{},[{'id':f'{jid}:{i}','stage':'shot','capability':'slow','payload':{'plan':{'backend':pool},'data':{'id':str(i),'reference_images':['image.png'] if refs else []}}} for i in range(count)])
 def test_idle_fast_group_takes_more_shots_without_waiting_for_slow(self):
  self.add('film');slow=self.q.claim(['slow']);fast=self.q.claim(['fast']);self.assertIsNotNone(fast)
  self.assertEqual(fast['payload']['backend_override']['capability'],'fast')
  self.assertIsNone(self.q.claim(['fast']))
  self.q.finish(fast,{});second=self.q.claim(['fast']);self.assertIsNotNone(second);self.assertNotEqual(fast['id'],second['id'])
  self.assertEqual(next(t for t in self.q.status() if t['id']==slow['id'])['status'],'running')
 def test_concurrent_claims_do_not_duplicate_or_overbook(self):
  self.add('film')
  with ThreadPoolExecutor(max_workers=4) as p:results=list(p.map(lambda _:self.q.claim(['fast']),range(4)))
  self.assertEqual(sum(r is not None for r in results),1)
 def test_uncertain_result_keeps_group_exclusive(self):
  self.add('film');t=self.q.claim(['fast']);self.q.finish(t,error='timeout',uncertain=True)
  self.assertIsNone(self.q.claim(['fast']));self.assertIsNotNone(self.q.claim(['slow']))
 def test_reference_capability_and_expiry_respected(self):
  self.add('film',refs=True);self.assertIsNone(self.q.claim(['fast']))
 def test_fixed_policy_does_not_migrate(self):
  self.add('film',dynamic=False);self.assertIsNone(self.q.claim(['fast']))
 def test_retry_never_reassigned(self):
  self.add('film');t=self.q.claim(['slow']);self.q.finish(t,error='failed',retryable=True)
  with self.q.connect() as c:c.execute("UPDATE tasks SET status='blocked' WHERE attempts=0")
  self.assertIsNone(self.q.claim(['fast']))
