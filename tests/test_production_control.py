import tempfile,unittest,json
from pathlib import Path
from workflow.queue import Queue
from workflow.production_control import withdrawn
class Controls(unittest.TestCase):
 def test_pause_blocks_submission_and_claim(self):
  with tempfile.TemporaryDirectory() as d:
   q=Queue(Path(d)/'queue.sqlite');q.submit('x','x',{'id':'x'},[{'id':'t','stage':'voice','payload':{}}])
   (Path(d)/'production_control.json').write_text(json.dumps({'paused':True}))
   self.assertIsNone(q.claim(['cpu']))
   with self.assertRaisesRegex(ValueError,'paused'):q.submit('y','y',{'id':'y'},[])
 def test_removed_family_cannot_reenter(self):
  with tempfile.TemporaryDirectory() as d:
   q=Queue(Path(d)/'queue.sqlite');(Path(d)/'production_control.json').write_text(json.dumps({'withdrawn_products':['x']}))
   self.assertTrue(withdrawn(d,'repair',{'repair':{'product_id':'x'}}))
   with self.assertRaisesRegex(ValueError,'removed'):q.submit('repair','r',{'id':'repair','product_id':'x'},[])
