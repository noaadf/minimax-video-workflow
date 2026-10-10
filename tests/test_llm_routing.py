import json
import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
from workflow.llm import ask_json, configuration
from workflow.codex_client import ask


class RoutingTests(unittest.TestCase):
    def config(self):
        return {'provider': 'slai', 'base_url': 'http://10.0.0.1:8080/v1',
                'api_key_env': 'CODEX_SLAI_API_KEY', 'model': 'gpt-6-astra',
                'transport': 'codex_cli', 'use_proxy': False}

    def test_default_is_glm_with_separate_vision_model(self):
        # Structural routing only: the live default model changed with the
        # 2026-10-06 cloud_ssh outage failover (glm-5.3 -> kimi-k3), and pinning
        # the name would break on every legitimate channel switch.
        with patch.dict(os.environ, {}, clear=True):
            c = configuration()
        self.assertTrue(isinstance(c['model'], str) and c['model'])
        self.assertIn('vision_model', c)
        self.assertEqual(c['transport'], 'anthropic_messages')
        self.assertIn('api_key_env', c)

    def test_missing_slai_key_never_uses_old_relay_key(self):
        with patch.dict(os.environ, {'CODEX_RELAY_API_KEY': 'old-provider'}, clear=True), \
                patch('workflow.codex_client.ask') as client, patch('workflow.llm.configuration', return_value=self.config()):
            with self.assertRaisesRegex(ValueError, 'CODEX_SLAI_API_KEY'):
                ask_json('test', {})
            client.assert_not_called()

    def test_explicit_endpoint_requires_its_own_explicit_key(self):
        with patch.dict(os.environ, {'PROMPT_API_BASE': 'https://example.test/v1',
                                    'CODEX_SLAI_API_KEY': 'slai-secret'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'PROMPT_API_KEY'):
                ask_json('test', {})

    def test_official_client_routes_model_and_images_without_secret_in_argv(self):
        commands = []
        def execute(command, **kwargs):
            commands.append(command)
            self.assertNotIn('private-key', ' '.join(command))
            self.assertEqual(kwargs['env']['CODEX_SLAI_API_KEY'], 'private-key')
            self.assertTrue(kwargs['start_new_session'])
            Path(command[command.index('-o')+1]).write_text('{"ok":true}')
            p=MagicMock();p.returncode=0;p.communicate.return_value=('{"type":"turn.completed","usage":{"output_tokens":4}}\n','');return p
        with patch.dict(os.environ, {'CODEX_SLAI_API_KEY': 'private-key'}, clear=True), \
                patch('workflow.codex_client.subprocess.Popen', execute):
            result, receipt = ask(self.config(), 'JSON only', {}, 500, ['frame.jpg'])
        self.assertTrue(result['ok'])
        self.assertIn('gpt-6-astra', commands[0]); self.assertIn('--image', commands[0])
        self.assertIn('--ignore-user-config', commands[0])
        self.assertEqual(receipt['transport'], 'official_codex_cli')

    def test_failed_client_never_falls_back_to_http(self):
        p=MagicMock();p.returncode=1;p.communicate.return_value=('','failed')
        with patch('workflow.codex_client.subprocess.Popen', return_value=p), \
                patch('urllib.request.urlopen') as http:
            with self.assertRaises(RuntimeError): ask(self.config(), 'test', {}, 100)
            http.assert_not_called()

    def test_reasoning_and_tier_forwarded_and_receipted(self):
        def execute(command, **kwargs):
            self.assertIn('model_reasoning_effort="high"', command)
            self.assertIn('service_tier="fast"', command)
            self.assertIn('model_providers.workflow_slai.request_max_retries=0', command)
            Path(command[command.index('-o')+1]).write_text('{"ok":true}')
            p=MagicMock();p.returncode=0;p.communicate.return_value=('{"type":"turn.completed"}\n','');return p
        c={**self.config(),'reasoning_effort':'high','service_tier':'fast','request_max_retries':0}
        with patch('workflow.codex_client.subprocess.Popen',execute):
            _,r=ask(c,'test',{},100)
        self.assertEqual(r['requested_reasoning_effort'],'high')
        self.assertEqual(r['requested_service_tier'],'fast')

    def test_timeout_is_explicit(self):
        p=MagicMock();p.pid=123;p.communicate.side_effect=[subprocess.TimeoutExpired('codex',1),('','')]
        with patch('workflow.codex_client.subprocess.Popen', return_value=p),patch('workflow.codex_client.os.killpg') as kill:
            with self.assertRaises(TimeoutError): ask(self.config(), 'test', {}, 100)
            kill.assert_called_once()
