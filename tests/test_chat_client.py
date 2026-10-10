import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch
from workflow.chat_client import ask


class ChatClientTests(unittest.TestCase):
    def config(self):
        return dict(provider='bigmodel', model='glm-5.3', base_url='https://example.test/v4',
                    api_key_env='TEST_KEY', reasoning_effort='high', thinking={'type':'enabled'})

    def invoke(self, finish='stop', text='{"ok":true}'):
        response = {'model':'glm-5.3','choices':[{'finish_reason':finish,'message':{'content':text}}]}
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = io.StringIO(json.dumps(response))
        with patch.dict(os.environ, {'TEST_KEY':'private'}), patch('urllib.request.build_opener',return_value=opener):
            result = ask(self.config(),'Return JSON',{},1000)
        request = opener.open.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(body['reasoning_effort'],'high')
        self.assertEqual(body['thinking'],{'type':'enabled'})
        self.assertNotIn('private',request.data.decode())
        opener.open.assert_called_once()
        return result

    def test_complete_json(self):
        answer, receipt = self.invoke()
        self.assertTrue(answer['ok'])
        self.assertEqual(receipt['model'],'glm-5.3')

    def test_truncated_result_is_not_success(self):
        with self.assertRaisesRegex(ValueError,'Incomplete'):self.invoke('length')

    def test_reasoning_without_final_text_is_not_success(self):
        with self.assertRaisesRegex(ValueError,'Missing final'):self.invoke(text=None)

    def test_unvalidated_images_fail_before_network(self):
        with patch('urllib.request.build_opener') as network:
            with self.assertRaisesRegex(ValueError,'capability'):ask(self.config(),'test',{},100,['unused.png'])
            network.assert_not_called()
