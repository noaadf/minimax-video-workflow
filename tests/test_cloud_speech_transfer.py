import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import cloud_speech


class CloudSpeechTransferTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.destination = self.root/'voice.mp3'
        self.destination.write_bytes(b'original accepted narration')
        self.audio = b'new verified narration'
        fixture = self.root/'fixture.mp3'; fixture.write_bytes(self.audio)
        request_id = cloud_speech.identity({'text': '你好。', 'voice': {}})
        self.receipt = {'request_id': request_id, 'path': f'/state/{request_id}/speech.mp3',
                        'sha256': cloud_speech.sha(fixture), 'bytes': len(self.audio)}
        self.cfg = {'host': 'example', 'port': 22, 'identity_file': '/test-key',
                    'code_root': '/runtime', 'state_root': '/state', 'python': 'python'}
        self.commands = []; self.transfer_code = 0; self.timeout = False

    def transport(self, command, **kwargs):
        self.commands.append(command)
        if command[0] == 'ssh':
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(self.receipt).encode())
        self.assertEqual(command[0], 'scp')
        Path(command[-1]).write_bytes(self.audio)
        if self.timeout:
            raise subprocess.TimeoutExpired(command, 60)
        return types.SimpleNamespace(returncode=self.transfer_code, stdout=b'')

    def synthesize(self, destination=None):
        return cloud_speech.synthesize('你好。', {}, destination or self.destination, self.cfg)

    def assert_single_transfer(self):
        self.assertEqual([c[0] for c in self.commands], ['ssh', 'scp'])
        self.assertEqual(list(self.root.glob('*.part.mp3')), [])

    def test_failed_partial_transfer_hash_or_timeout_preserves_original(self):
        for kind in ('exit_code', 'wrong_hash', 'timeout'):
            with self.subTest(kind=kind):
                self.destination.write_bytes(b'original accepted narration')
                self.commands = []; self.audio = b'partial or wrong narration'
                self.transfer_code = 1 if kind == 'exit_code' else 0
                self.timeout = kind == 'timeout'
                with patch('workflow.cloud_speech.subprocess.run', side_effect=self.transport), \
                     patch('workflow.media.duration') as duration:
                    with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                        self.synthesize()
                    duration.assert_not_called()
                self.assertEqual(self.destination.read_bytes(), b'original accepted narration')
                self.assert_single_transfer()

    def test_invalid_duration_cannot_replace_original(self):
        for seconds in (float('nan'), float('inf'), -float('inf'), 0, -1):
            with self.subTest(seconds=seconds):
                self.destination.write_bytes(b'original accepted narration'); self.commands = []
                with patch('workflow.cloud_speech.subprocess.run', side_effect=self.transport), \
                     patch('workflow.media.duration', return_value=seconds):
                    with self.assertRaises(ValueError):
                        self.synthesize()
                self.assertEqual(self.destination.read_bytes(), b'original accepted narration')
                self.assert_single_transfer()

    def test_probe_failure_preserves_original_and_does_not_retry(self):
        with patch('workflow.cloud_speech.subprocess.run', side_effect=self.transport), \
             patch('workflow.media.duration', side_effect=RuntimeError('probe failed')):
            with self.assertRaisesRegex(RuntimeError, 'probe failed'):
                self.synthesize()
        self.assertEqual(self.destination.read_bytes(), b'original accepted narration')
        self.assert_single_transfer()

    def test_success_publishes_only_verified_audio_and_keeps_remote_receipt(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                destination = self.root/f'voice-{existing}.mp3'
                if existing:
                    destination.write_bytes(b'previous narration')
                self.commands = []
                with patch('workflow.cloud_speech.subprocess.run', side_effect=self.transport), \
                     patch('workflow.media.duration', return_value=1.2) as duration:
                    result = self.synthesize(destination)
                self.assertEqual(result, self.receipt)
                self.assertEqual(destination.read_bytes(), self.audio)
                self.assertEqual(cloud_speech.sha(destination), self.receipt['sha256'])
                self.assertNotEqual(Path(self.commands[-1][-1]), destination)
                self.assertNotEqual(Path(duration.call_args.args[0]), destination)
                self.assert_single_transfer()

    def test_wrong_request_or_remote_path_blocks_before_transfer(self):
        original = dict(self.receipt)
        for field in ('request_id', 'path'):
            with self.subTest(field=field):
                self.receipt = {**original, field: 'different-request'}; self.commands = []
                with patch('workflow.cloud_speech.subprocess.run', side_effect=self.transport), \
                     patch('workflow.media.duration') as duration:
                    with self.assertRaisesRegex(ValueError, 'remote speech receipt'):
                        self.synthesize()
                    duration.assert_not_called()
                self.assertEqual(self.destination.read_bytes(), b'original accepted narration')
                self.assertEqual([c[0] for c in self.commands], ['ssh'])
