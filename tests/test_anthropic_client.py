import io
import json
import os
import unittest
from unittest.mock import patch,MagicMock
from workflow.anthropic_client import ask,credential,read_stream,ModelResultError
import time


class AnthropicTests(unittest.TestCase):
    def test_stream_error_has_safe_diagnostics_without_provider_message(self):
        line=b'data: '+json.dumps({'type':'error','error':{'type':'rate_limit_error','code':429,
            'message':'private prompt and credential secret'}}).encode()
        with self.assertRaises(ModelResultError) as caught:read_stream([line],time.monotonic()+1)
        diagnostic=caught.exception.diagnostic
        self.assertEqual(diagnostic['receipt']['provider_error_code'],429)
        self.assertNotIn('secret',json.dumps(diagnostic))
    def config(self):
        return dict(provider='bigmodel',model='glm-5.3-flash',api_key_env='TEST_GLM_KEY',base_url='https://example.test/api/anthropic')

    def test_json_output_does_not_include_thinking(self):
        response={'model':'glm-5.3-flash','stop_reason':'end_turn','content':[{'type':'thinking','thinking':'ignore'},{'type':'text','text':'{"ok":true}'}]}
        opener=MagicMock();opener.open.return_value.__enter__.return_value=io.StringIO(json.dumps(response))
        with patch.dict(os.environ,{'TEST_GLM_KEY':'secret'}),patch('urllib.request.build_opener',return_value=opener):
            answer,receipt=ask(self.config(),'test',{},2048)
        self.assertEqual(answer,{'ok':True});self.assertEqual(receipt['model'],'glm-5.3-flash')
        opener.open.assert_called_once()
        self.assertNotIn('secret',opener.open.call_args.args[0].data.decode())

    def test_truncation_not_accepted(self):
        opener=MagicMock();opener.open.return_value.__enter__.return_value=io.StringIO('{"stop_reason":"max_tokens"}')
        with patch.dict(os.environ,{'TEST_GLM_KEY':'secret'}),patch('urllib.request.build_opener',return_value=opener):
            with self.assertRaises(ModelResultError) as caught:ask(self.config(),'test',{},50)
        self.assertEqual(caught.exception.diagnostic['receipt']['stop_reason'],'max_tokens')
        self.assertEqual(caught.exception.diagnostic['code'],'incomplete_output')
        self.assertNotIn('secret',json.dumps(caught.exception.diagnostic))

    def test_unverified_images_rejected(self):
        with self.assertRaisesRegex(ValueError,'image'):ask(self.config(),'test',{},50,['unused.png'])

    def test_missing_key_no_fallback(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'credential'):credential(self.config())

    def test_stream_requires_final_event_and_ignores_thinking(self):
        events=[{'type':'message_start','message':{'model':'glm','usage':{'input_tokens':2}}},
                {'type':'content_block_delta','index':0,'delta':{'type':'thinking_delta','thinking':'hidden'}},
                {'type':'content_block_start','index':1,'content_block':{'type':'text','text':''}},
                {'type':'content_block_delta','index':1,'delta':{'type':'text_delta','text':'{"ok":true}'}},
                {'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':4}},
                {'type':'message_stop'}]
        lines=[b'data: '+json.dumps(x).encode()+b'\n' for x in events]
        r=read_stream(lines,time.monotonic()+1)
        self.assertEqual(r['content'],[{'type':'text','text':'{"ok":true}'}])
        with self.assertRaisesRegex(ValueError,'Incomplete'):read_stream(lines[:-1],time.monotonic()+1)
