import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from types import SimpleNamespace
from workflow.cloud_llm import request_identity,submit
from workflow.plan import write_json
from workflow.cloud_llm_client import recovery_request,ask
class TransportAttemptTests(unittest.TestCase):
 def setUp(self):
  self.req={'instruction':'same exact review','data':{'manifest_sha256':'abc'},'max_tokens':3500,'model':'glm-5.3','images':[]}
  self.prior={'id':request_identity(self.req),'state':'failed','error_type':'ModelResultError','diagnostic':{'code':'provider_stream_error','receipt':{'provider_error_type':'api_error'}}}
 def test_single_deterministic_attempt_preserves_every_provider_field(self):
  r=recovery_request(self.req,self.prior)
  self.assertNotEqual(request_identity(r),request_identity(self.req))
  self.assertEqual(r,recovery_request(self.req,self.prior))
  self.assertEqual({k:v for k,v in r.items() if k!='transport_attempt'},self.req)
  self.assertEqual(r['transport_attempt']['attempt'],1)
  with self.assertRaises(ValueError):recovery_request(r,{**self.prior,'id':request_identity(r)})
 def test_fail_closed_for_pending_verdict_invalid_json_permission_and_other_identity(self):
  cases=[{**self.prior,'state':'calling'},{**self.prior,'state':'completed','result':{'answer':{'verdict':'revise'}}},{**self.prior,'result':{'answer':{'verdict':'pass'}}},{**self.prior,'id':'0'*64},{**self.prior,'diagnostic':{'code':'invalid_json'}},{**self.prior,'diagnostic':{'code':'incomplete_output','receipt':{'stop_reason':'max_tokens'}}},{**self.prior,'diagnostic':{'code':'provider_http_error','http_status':401}},{**self.prior,'diagnostic':None}]
  for x in cases:
   with self.subTest(x=x),self.assertRaises(ValueError):recovery_request(self.req,x)
 def test_opt_in_checks_remote_terminal_evidence_before_submit_and_saves_link(self):
  with tempfile.TemporaryDirectory() as t:
   evidence=Path(t)/'receipt.json';calls=[];rid=request_identity(recovery_request(self.req,self.prior))
   config={'model':'glm-5.3','cloud_worker':{'port':22,'identity_file':'/tmp/key','host':'host','code_root':'/code','state_root':'/state'}}
   def run(argv,**kw):
    calls.append((argv,kw));cmd=argv[-1]
    if ' status ' in cmd:return SimpleNamespace(returncode=0,stdout=json.dumps(self.prior))
    self.assertIn(rid,cmd);req=json.loads(kw['input']);self.assertEqual(req['instruction'],self.req['instruction']);self.assertEqual(req['data'],self.req['data'])
    return SimpleNamespace(returncode=0,stdout=json.dumps({'id':rid,'state':'completed','result':{'answer':{'verdict':'revise'},'receipt':{'cloud_job_id':rid}}}))
   with patch('workflow.cloud_llm_client.subprocess.run',side_effect=run):
    result,_=ask(config,self.req['instruction'],self.req['data'],3500,technical_recovery_path=evidence)
   self.assertEqual(result['verdict'],'revise');self.assertEqual(len(calls),2)
   self.assertEqual(json.loads(evidence.read_text())['recovery_job_id'],rid)
 def test_transient_http_is_explicitly_allowed(self):
  self.assertEqual(recovery_request(self.req,{**self.prior,'diagnostic':{'code':'provider_http_error','http_status':503}})['transport_attempt']['attempt'],1)
 def test_real_durable_submit_reuses_recovery_and_preserves_original_failure(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);spawn=Mock(return_value=SimpleNamespace(pid=99))
   submit(root,self.req,spawn);old=root/'jobs'/request_identity(self.req)/'status.json'
   write_json(old,self.prior);before=old.read_bytes()
   r=recovery_request(self.req,self.prior)
   first=submit(root,r,spawn);again=submit(root,r,spawn)
   self.assertEqual(first,again);self.assertEqual(spawn.call_count,2)
   self.assertEqual(old.read_bytes(),before)
if __name__=='__main__':unittest.main()
