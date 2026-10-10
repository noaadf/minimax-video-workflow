import sqlite3,unittest
from unittest.mock import patch
from workflow.queue import begin_immediate
class BusyTests(unittest.TestCase):
 def connection(self,failures,code=sqlite3.SQLITE_BUSY):
  class Conn:
   calls=0
   def execute(self,sql):
    if sql=='PRAGMA busy_timeout':return self
    if sql=='BEGIN IMMEDIATE':
     self.calls+=1
     if self.calls<=failures:
      e=sqlite3.OperationalError('database is locked');e.sqlite_errorcode=code;raise e
    return self
   def fetchone(self):return [30000]
  return Conn()
 def test_transient_busy_retries_only_begin(self):
  c=self.connection(2)
  with patch('workflow.queue.time.sleep'):begin_immediate(c)
  self.assertEqual(c.calls,3)
 def test_permanent_busy_is_bounded(self):
  c=self.connection(20)
  with patch('workflow.queue.time.sleep'),self.assertRaises(sqlite3.OperationalError):begin_immediate(c)
  self.assertEqual(c.calls,4)
 def test_non_busy_not_retried(self):
  c=self.connection(20,sqlite3.SQLITE_ERROR)
  with self.assertRaises(sqlite3.OperationalError):begin_immediate(c)
  self.assertEqual(c.calls,1)
