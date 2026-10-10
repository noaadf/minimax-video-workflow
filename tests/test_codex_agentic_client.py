"""codex_agentic transport: subscription invocation shape, JSON framing, failures."""
import unittest
from unittest.mock import patch, MagicMock
from workflow import codex_agentic_client
from workflow.codex_agentic_client import ask


class CodexAgenticTest(unittest.TestCase):
    def cfg(self):
        return {'model': 'gpt-6.1-sol', 'workspace': '/tmp/ws', 'sandbox': 'read-only',
                'reasoning_effort': 'high', 'total_timeout_seconds': 60}

    def test_command_shape_subscription_mode(self):
        fake = MagicMock(returncode=0, stdout='{"verdict": "pass"}')
        with patch('workflow.codex_agentic_client.subprocess.run', return_value=fake) as run:
            ask(self.cfg(), 'judge', {'x': 1}, 4000)
        cmd = run.call_args.args[0]
        self.assertTrue(cmd[0].endswith('/bin/codex'))
        self.assertIn('exec', cmd)
        self.assertNotIn('--ignore-user-config', cmd)      # subscription auth intact
        self.assertNotIn('model_provider', ' '.join(cmd))  # no API-gateway override
        self.assertIn('-s', cmd)
        self.assertIn('read-only', cmd)
        self.assertIn('web_search="disabled"', cmd)

    def test_workspace_and_evidence_framing(self):
        fake = MagicMock(returncode=0, stdout='{"ok": true}')
        with patch('workflow.codex_agentic_client.subprocess.run', return_value=fake) as run:
            ask(self.cfg(), 'research then judge', {'y': 2}, 4000)
        prompt = run.call_args.args[0][-1]
        self.assertIn('/tmp/ws', prompt)
        self.assertIn('EXACTLY ONE JSON object', prompt)
        self.assertIn('Do NOT modify', prompt)

    def test_web_search_enabled_via_config(self):
        cfg = self.cfg(); cfg['web_search'] = True
        fake = MagicMock(returncode=0, stdout='{"ok": true}')
        with patch('workflow.codex_agentic_client.subprocess.run', return_value=fake) as run:
            ask(cfg, 'i', {}, 100)
        self.assertIn('web_search="enabled"', run.call_args.args[0])

    def test_nonzero_exit_raises_and_json_missing_raises(self):
        with patch('workflow.codex_agentic_client.subprocess.run',
                   return_value=MagicMock(returncode=1, stdout='', stderr='boom')):
            with self.assertRaisesRegex(RuntimeError, 'exited 1'):
                ask(self.cfg(), 'i', {}, 100)
        with patch('workflow.codex_agentic_client.subprocess.run',
                   return_value=MagicMock(returncode=0, stdout='all prose', stderr='')):
            with self.assertRaises(ValueError):
                ask(self.cfg(), 'i', {}, 100)


if __name__ == '__main__':
    unittest.main()
