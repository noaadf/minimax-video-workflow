"""Unknown transport fails before credentials or external execution."""
import unittest
from unittest.mock import patch
from workflow.core.failures import Disposition, WorkflowFailure
from workflow.llm import ask_json


class TransportContractTests(unittest.TestCase):
    def test_unknown_and_missing_transport_fail_explicitly(self):
        for config in ({'transport': 'response_typo'}, {},
                       {'transport': 'unknown', 'execution': 'cloud_ssh'}):
            with self.subTest(config=config), \
                 patch('workflow.relay_credentials.credential') as credential, \
                 patch('workflow.cloud_llm_client.ask') as cloud, \
                 patch('urllib.request.build_opener') as opener, \
                 self.assertRaises(WorkflowFailure) as caught:
                ask_json('fake', {}, config=config)
            self.assertEqual(caught.exception.code, 'config_invalid')
            self.assertEqual(caught.exception.disposition, Disposition.TERMINAL)
            credential.assert_not_called(); cloud.assert_not_called(); opener.assert_not_called()
