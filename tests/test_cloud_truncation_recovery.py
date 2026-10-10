import unittest
from workflow.cloud_llm import request_identity
from workflow.cloud_llm_client import truncation_recovery_request
class TruncationTests(unittest.TestCase):
 def setUp(self):
  self.req={'instruction':'strict review','data':{'manifest_sha':'abc'},'model':'glm-5.3','max_tokens':12000,'images':[]}
  self.prior={'id':request_identity(self.req),'state':'failed','diagnostic':{'code':'incomplete_output','receipt':{'stop_reason':'max_tokens'}}}
 def test_only_reasoning_changed_deterministic_and_nonchainable(self):
  out=truncation_recovery_request(self.req,self.prior)
  self.assertEqual(out,truncation_recovery_request(self.req,self.prior))
  self.assertEqual({k:v for k,v in out.items() if k not in ('thinking','transport_attempt')},self.req)
  self.assertEqual(out['thinking'],{'type':'disabled'})
  with self.assertRaises(ValueError):truncation_recovery_request(out,{**self.prior,'id':request_identity(out)})
 def test_no_verdict_or_other_error_can_be_retried(self):
  for prior in [{**self.prior,'result':{'answer':{'verdict':'revise'}}},{**self.prior,'state':'calling'},{**self.prior,'diagnostic':{'code':'invalid_json'}},{**self.prior,'diagnostic':{'code':'incomplete_output','receipt':{'stop_reason':'end_turn'}}},{**self.prior,'id':'bad'}]:
   with self.subTest(prior=prior),self.assertRaises(ValueError):truncation_recovery_request(self.req,prior)

 def test_ask_fetches_old_identity_then_submits_disabled_attempt(self):
  import json,tempfile
  from pathlib import Path
  from types import SimpleNamespace
  from unittest.mock import patch
  from workflow.cloud_llm_client import ask
  out=truncation_recovery_request(self.req,self.prior);jid=request_identity(out);calls=[]
  config={'model':'glm-5.3','cloud_min_output_tokens':12000,'cloud_worker':{'port':22,'identity_file':'/tmp/key','host':'host','code_root':'/code','state_root':'/state'}}
  def run(argv,**kw):
   calls.append(argv[-1])
   if ' status ' in argv[-1]:
    self.assertIn(self.prior['id'],argv[-1]);value=self.prior
   else:
    self.assertIn(jid,argv[-1]);self.assertEqual(json.loads(kw['input']),out)
    value={'state':'completed','result':{'answer':{'verdict':'revise'},'receipt':{'model':'glm-5.3'}}}
   return SimpleNamespace(returncode=0,stdout=json.dumps(value))
  with tempfile.TemporaryDirectory() as t,patch('workflow.cloud_llm_client.subprocess.run',side_effect=run):
   path=Path(t)/'recovery.json'
   answer,_=ask(config,self.req['instruction'],self.req['data'],3500,disable_thinking_recovery_path=path)
   self.assertEqual(answer['verdict'],'revise');record=json.loads(path.read_text())
   self.assertEqual(record['old_thinking'],'provider_default');self.assertEqual(record['max_tokens_unchanged'],12000)
  self.assertEqual(len(calls),2)
