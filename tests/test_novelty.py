import unittest,tempfile
from pathlib import Path
from workflow.queue import Queue
class NoveltyTests(unittest.TestCase):
 def test_duplicates_blocked_new_angle_and_repairs_allowed(self):
  with tempfile.TemporaryDirectory() as d:
   q=Queue(Path(d)/'q');text='这座大桥跨过峡谷，工程师先把主缆拉过去，再把钢梁一段段接起来。人们以前绕路几个小时，现在能够直接过河，连起两岸的生活。'
   p={'id':'a','narration':[{'text':text}],'novelty_policy':{'enabled':True}}
   q.submit('a','a',p,[])
   with self.assertRaisesRegex(ValueError,'editorial duplicate'):q.submit('b','b',{**p,'id':'b'},[])
   q.submit('c','c',{**p,'id':'c','narration':[{'text':'同一座大桥附近的村民转向旅游服务，一家小店开始销售当地农产品。游客愿意停下来吃饭买特产，生意如何持续，还取决于服务品质与真实需求。'}]},[])
   q.submit('a-repair','r',{**p,'id':'a-repair','parent_job_id':'a','product_id':'a'},[])
 def test_paused_subject(self):
  with tempfile.TemporaryDirectory() as d:
   q=Queue(Path(d)/'q')
   with self.assertRaisesRegex(ValueError,'subject paused'):q.submit('b','b',{'id':'b','title':'花江峡谷大桥','novelty_policy':{'enabled':True,'paused_subjects':['花江峡谷大桥']}},[])
 def test_repeated_footage_hashes_blocked_even_at_different_paths(self):
  with tempfile.TemporaryDirectory() as d:
   q=Queue(Path(d)/'q')
   q.submit('a','a',{'id':'a','shots':[{'source_path':'one'},{'source_path':'two'}],'input_sha256':{'one':'h1','two':'h2'}},[])
   p={'id':'b','novelty_policy':{'enabled':True},'shots':[{'source_path':'copy1'},{'source_path':'copy2'}],'input_sha256':{'copy1':'h1','copy2':'h2'}}
   with self.assertRaisesRegex(ValueError,'repeated footage'):q.submit('b','b',p,[])
