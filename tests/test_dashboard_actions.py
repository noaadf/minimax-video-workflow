import json,tempfile,threading,unittest,urllib.request,urllib.error,uuid
from pathlib import Path
from http.server import ThreadingHTTPServer
from workflow.queue import Queue
from workflow.plan import sha256,write_json
from workflow.dashboard_actions import export_film,request_repair,repair_requests,ActionError
from workflow.dashboard import make_handler

class ActionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'output/production';self.root.mkdir(parents=True)
  Queue(self.root/'queue.sqlite');self.f=self.root/'film';self.f.mkdir();(self.f/'video.mp4').write_bytes(b'video');(self.f/'cover.png').write_bytes(b'cover')
  self.sha=sha256(self.f/'video.mp4');write_json(self.root/'batch/latest_review_bundle.json',{'videos':[{'id':'film','title':'中国桥','video':str(self.f/'video.mp4')}]})
 def tearDown(self):self.tmp.cleanup()
 def test_export_verified_idempotent_and_preserves_changes(self):
  dest=Path(self.tmp.name)/'upload folder'
  a=export_film(self.root,'film',self.sha,str(dest));b=export_film(self.root,'film',self.sha,str(dest));self.assertEqual(a,b)
  p=Path(a['folder'])/'视频.mp4';self.assertEqual(sha256(p),self.sha);p.write_bytes(b'user edited')
  with self.assertRaises(ActionError):export_film(self.root,'film',self.sha,str(dest))
  self.assertEqual(p.read_bytes(),b'user edited')
 def test_repair_deduplicated_and_version_bound(self):
  rid=str(uuid.uuid4());a=request_repair(self.root,'film',self.sha,'12秒桥没对齐',12,rid);b=request_repair(self.root,'film',self.sha,'12秒桥没对齐',12,rid)
  self.assertEqual(a,b);self.assertEqual(len(repair_requests(self.root)),1)
  with self.assertRaises(ActionError):request_repair(self.root,'film','old','wrong')
  with self.assertRaises(ActionError):request_repair(self.root,'film',self.sha,'changed',12,rid)
 def test_http_write_requires_token_and_same_origin(self):
  server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.root));thread=threading.Thread(target=server.serve_forever);thread.start()
  url=f'http://127.0.0.1:{server.server_port}';opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  try:
   token=json.load(opener.open(url+'/api/delivery-actions'))['token'];data=json.dumps({'job_id':'film','video_sha256':self.sha,'note':'test'}).encode()
   for headers in ({},{'X-Workflow-Token':token,'Origin':'https://evil.test'}):
    with self.assertRaises(urllib.error.HTTPError) as e:opener.open(urllib.request.Request(url+'/api/request-repair',data=data,headers={'Content-Type':'application/json',**headers}))
    self.assertEqual(e.exception.code,403)
   r=json.load(opener.open(urllib.request.Request(url+'/api/request-repair',data=data,headers={'Content-Type':'application/json','X-Workflow-Token':token,'Origin':url})))
   self.assertEqual(r['status'],'pending')
  finally:server.shutdown();server.server_close();thread.join()
