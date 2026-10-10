import io,json,threading,concurrent.futures
from scripts import verify_external_image_apis as api

def setup(monkeypatch,tmp_path):
 monkeypatch.setattr(api,'OUT',tmp_path/'out');monkeypatch.setattr(api,'ROOT',tmp_path)
 (tmp_path/'config.json').write_text('{}');monkeypatch.setattr(api,'credential',lambda c:'secret-test-key')

def test_concurrent_submit_once_and_restart(monkeypatch,tmp_path):
 setup(monkeypatch,tmp_path);entered=threading.Event();release=threading.Event();calls=[]
 class Opener:
  def open(self,*a,**kw):
   calls.append(1);entered.set();assert release.wait(5)
   raise TimeoutError()
 monkeypatch.setattr(api.urllib.request,'build_opener',lambda *a:Opener())
 args=('probe','config.json','https://example.test/images',{'model':'test'})
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
  first=pool.submit(api.run,*args);assert entered.wait(5)
  second=pool.submit(api.run,*args).result(timeout=5)
  assert second['state']=='attempt_in_progress_not_resubmitted'
  release.set();assert first.result()['state']=='uncertain_or_artifact_error_do_not_resubmit'
 assert api.run(*args)['state']=='existing_attempt_not_resubmitted'
 assert len(calls)==1

def test_no_image_error_is_redacted(monkeypatch,tmp_path):
 setup(monkeypatch,tmp_path)
 class Response(io.BytesIO):status=200
 class Opener:
  def open(self,*a,**kw):return Response(json.dumps({'error':{'message':'secret-test-key'},'data':[]}).encode())
 monkeypatch.setattr(api.urllib.request,'build_opener',lambda *a:Opener())
 result=api.run('probe','config.json','https://example.test/images',{'model':'test'})
 assert result['state']=='no_image_returned'
 assert 'secret-test-key' not in (api.OUT/'probe/receipt.json').read_text()
 assert 'elapsed_seconds' in result

if __name__=='__main__':
 import tempfile
 from pathlib import Path
 from unittest.mock import patch
 from contextlib import ExitStack
 class Patches:
  def __init__(self,stack):self.stack=stack
  def setattr(self,obj,name,value):self.stack.enter_context(patch.object(obj,name,value))
 for test in (test_concurrent_submit_once_and_restart,test_no_image_error_is_redacted):
  with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
   test(Patches(stack),Path(folder));print(test.__name__+': PASS')
