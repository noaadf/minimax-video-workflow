import io,json,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import Mock,patch
from workflow.cloud_llm import submit,request_identity,failure_diagnostic,worker

class CloudTests(unittest.TestCase):
    def request(self):return {'model':'glm-5.3','max_tokens':12000,'instruction':'Return JSON','data':{'test':1},'images':[]}
    def test_duplicate_submit_never_spawns_twice(self):
        with tempfile.TemporaryDirectory() as t:
            spawn=Mock(return_value=Mock(pid=123))
            a=submit(t,self.request(),spawn);b=submit(t,self.request(),spawn)
            self.assertEqual(a,b);spawn.assert_called_once()
    def test_uncertain_spawn_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as t:
            spawn=Mock(side_effect=OSError('uncertain'))
            with self.assertRaises(OSError):submit(t,self.request(),spawn)
            state=submit(t,self.request(),spawn)
            self.assertEqual(state['state'],'submitted');spawn.assert_called_once()
    def test_input_changed_gets_different_identity(self):
        a=self.request();b={**a,'data':{'test':2}}
        self.assertNotEqual(request_identity(a),request_identity(b))
    def test_unbounded_budget_and_other_model_rejected(self):
        for extra in ({'max_tokens':100000},{'model':'unapproved'},{'images':[{'suffix':'.sh','data':''}]}):
            with self.assertRaises(ValueError):request_identity({**self.request(),**extra})

    def test_http_failure_classification_without_request_or_body_leak(self):
        for code,category in [(400,'invalid_request'),(401,'credentials_or_permission'),
                              (413,'request_too_large'),(429,'rate_limited'),
                              (500,'provider_unavailable'),(504,'provider_timeout')]:
            body=io.BytesIO(b'{"message":"PRIVATE BODY TOKEN"}')
            error=urllib.error.HTTPError('https://example.test/?key=PRIVATE_URL_TOKEN',code,
                'PRIVATE_REASON_TOKEN',{'Authorization':'PRIVATE_HEADER_TOKEN','Retry-After':'30'},body)
            diagnostic=failure_diagnostic(error)
            self.assertEqual(diagnostic['http_status'],code)
            self.assertEqual(diagnostic['category'],category)
            self.assertEqual(diagnostic['retry_after_seconds'],30)
            self.assertFalse(diagnostic['automatic_replay'])
            self.assertNotIn('PRIVATE',json.dumps(diagnostic))
            self.assertEqual(body.tell(),0)

    def test_arbitrary_retry_after_text_is_not_retained(self):
        error=urllib.error.HTTPError('https://example.test/',429,'error',{'Retry-After':'secret text'},None)
        self.assertNotIn('retry_after_seconds',failure_diagnostic(error))

    def test_existing_model_diagnostic_preserved(self):
        error=ValueError('unused');error.diagnostic={'code':'invalid_json','receipt':{'cloud_job_id':'existing'}}
        self.assertEqual(failure_diagnostic(error),error.diagnostic)

    def test_other_exception_does_not_invent_http_status(self):
        self.assertIsNone(failure_diagnostic(TimeoutError('transport')))

    def test_worker_commits_failed_http_status_and_does_not_replay(self):
        with tempfile.TemporaryDirectory() as t,patch.dict('os.environ',{}):
            root=Path(t);request=self.request();jid=request_identity(request)
            folder=root/'jobs'/jid;folder.mkdir(parents=True)
            (folder/'request.json').write_text(json.dumps(request))
            (folder/'status.json').write_text(json.dumps({'id':jid,'state':'submitted'}))
            credential=root/'credential.txt';credential.write_text('PRIVATE_CREDENTIAL')
            (root/'config.json').write_text(json.dumps({'api_key_env':'TEST_REVIEW_HTTP_DIAGNOSTIC','credential_file':str(credential)}))
            error=urllib.error.HTTPError('https://example.test/',429,'rate limited',{'Retry-After':'15'},None)
            with patch('workflow.anthropic_client.ask',side_effect=error) as ask,patch('workflow.cloud_dns.install'):
                worker(root,jid);worker(root,jid)
                ask.assert_called_once()
            status=json.loads((folder/'status.json').read_text())
            self.assertEqual(status['state'],'failed')
            self.assertEqual(status['diagnostic']['http_status'],429)
            self.assertFalse((folder/'result.json').exists())
            self.assertNotIn('PRIVATE',json.dumps(status))
