import threading,time,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
from workflow.queue import Queue

class CompletionLockTests(unittest.TestCase):
 def test_completion_and_heartbeat_wait_for_claim_transaction(self):
  with tempfile.TemporaryDirectory() as d:
   q=Queue(Path(d)/'q.sqlite');entered=[]
   for name,inner,args in [('finish','_finish',({'id':'test'},)),('heartbeat','_heartbeat',('test','token'))]:
    with patch.object(q,inner,side_effect=lambda *a:entered.append(name)):
     q._claim_lock.acquire();t=threading.Thread(target=getattr(q,name),args=args);t.start();time.sleep(.03)
     self.assertNotIn(name,entered);q._claim_lock.release();t.join(1);self.assertFalse(t.is_alive());self.assertIn(name,entered)
 def test_lock_released_on_completion_error(self):
  with tempfile.TemporaryDirectory() as d:
   q=Queue(Path(d)/'q.sqlite')
   with patch.object(q,'_finish',side_effect=ValueError('fail')):
    with self.assertRaises(ValueError):q.finish({})
   self.assertTrue(q._claim_lock.acquire(timeout=.1));q._claim_lock.release()

class ResultCommitTests(unittest.TestCase):
 def test_successful_external_work_not_marked_failed_or_reexecuted_on_commit_error(self):
  import sqlite3,contextlib,io
  from workflow.runner import work
  stop=threading.Event();receipt={'elapsed_seconds':1,'report_path':'already-produced.json'}
  class Q:
   calls=[]
   def claim(self,*a,**k):return {'id':'q','token':'t'}
   def finish(self,task,**kwargs):
    self.calls.append(kwargs)
    if len(self.calls)==1:raise sqlite3.OperationalError('database is locked')
    stop.set()
  q=Q()
  with patch('workflow.runner.execute',return_value=receipt) as execute,contextlib.redirect_stdout(io.StringIO()):work(q,['cpu'],stop)
  self.assertEqual(execute.call_count,1)
  self.assertTrue(q.calls[1]['uncertain']);self.assertFalse(q.calls[1]['retryable']);self.assertEqual(q.calls[1]['result'],receipt)
  self.assertTrue(q.calls[1]['error'].startswith('commit_result:'))
