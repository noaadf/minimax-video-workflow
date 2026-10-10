"""claude_cli transport: JSON extraction, receipt shape, failure surfaces."""
import json
import unittest
from unittest.mock import patch, MagicMock
from workflow.claude_cli_client import ask, _extract_json


class ExtractJsonTest(unittest.TestCase):
    def test_balanced_object_from_prose(self):
        self.assertEqual(_extract_json('Sure! {"a": 1} hope that helps'), {'a': 1})

    def test_nested_and_second_object(self):
        self.assertEqual(_extract_json('nope {"o": {"b": 2}, "c": 3} tail'), {'o': {'b': 2}, 'c': 3})

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            _extract_json('all prose, no braces')


class AskTest(unittest.TestCase):
    def _envelope(self, result_text):
        return json.dumps({'type': 'result', 'result': result_text, 'model': 'claude-relay-test',
                           'subtype': 'success', 'usage': {'input_tokens': 11, 'output_tokens': 7},
                           'total_cost_usd': 0.01})

    def test_ask_parses_result_and_receipt(self):
        cfg = {'model': 'claude-opus-test', 'cli_binary': 'claude-relay', 'timeout_seconds': 30}
        fake = MagicMock(returncode=0, stdout=self._envelope('{"verdict": "pass", "checks": {}}'))
        with patch('workflow.claude_cli_client.subprocess.run', return_value=fake) as run:
            answer, receipt = ask(cfg, 'review this', {'x': 1}, 4000)
        self.assertEqual(answer, {'verdict': 'pass', 'checks': {}})
        self.assertEqual(receipt['provider'], 'claude_cli')
        self.assertEqual(receipt['model'], 'claude-relay-test')
        self.assertEqual(receipt['usage']['output_tokens'], 7)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:3], ['claude-relay', '--model', 'claude-opus-test'])
        self.assertIn('--output-format', cmd)

    def test_nonzero_exit_raises(self):
        cfg = {'model': 'm'}
        fake = MagicMock(returncode=2, stdout='', stderr='boom')
        with patch('workflow.claude_cli_client.subprocess.run', return_value=fake):
            with self.assertRaisesRegex(RuntimeError, 'exited 2'):
                ask(cfg, 'i', {}, 100)


if __name__ == '__main__':
    unittest.main()
