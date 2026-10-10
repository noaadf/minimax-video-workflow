import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow.audio_cache import speech
from workflow.plan import digest, sha256, write_json


class AudioCacheCommitRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.identity = digest({'schema': 1, 'engine': 'edge-tts', 'text': 'same', 'voice': {}})
        self.folder = self.root/'speech'/self.identity
        self.env = patch.dict(os.environ, {'WORKFLOW_AUDIO_CACHE': str(self.root)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.duration = patch('workflow.media.duration', return_value=1)
        self.probe = self.duration.start(); self.addCleanup(self.duration.stop)
        self.calls = []

    async def produce(self, path):
        self.calls.append(path)
        path.write_bytes(b'completed audio')

    async def test_completed_audio_recovers_after_receipt_commit_failure_without_new_producer(self):
        replace = os.replace
        def fail_receipt_commit(source, dest):
            if Path(dest) == self.folder/'receipt.json':
                raise OSError('simulated receipt commit failure')
            return replace(source, dest)
        target = self.root/'first.mp3'; target.write_bytes(b'previous destination')
        # Cover both the old direct write and the new journal-to-receipt rename.
        real_write = write_json
        def fail_old_direct_write(path, value):
            if Path(path) == self.folder/'receipt.json':
                raise OSError('simulated receipt commit failure')
            return real_write(path, value)
        with patch('workflow.audio_cache.os.replace', side_effect=fail_receipt_commit), \
             patch('workflow.audio_cache.write_json', side_effect=fail_old_direct_write):
            with self.assertRaises(OSError):
                await speech('same', {}, target, self.produce)
        self.assertEqual(target.read_bytes(), b'previous destination')
        self.assertEqual((self.folder/'audio.mp3').read_bytes(), b'completed audio')
        results = await asyncio.gather(
            speech('same', {}, self.root/'second.mp3', self.produce),
            speech('same', {}, self.root/'third.mp3', self.produce))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(results, [True, True])
        self.assertEqual(self.probe.call_count, 1)
        for name in ('second.mp3', 'third.mp3'):
            self.assertEqual((self.root/name).read_bytes(), b'completed audio')
        self.assertEqual(json.loads((self.folder/'receipt.json').read_text())['sha256'], sha256(self.folder/'audio.mp3'))

    async def test_failed_journal_write_preserves_existing_audio_and_destination(self):
        self.folder.mkdir(parents=True)
        (self.folder/'audio.mp3').write_bytes(b'old uncommitted audio')
        target = self.root/'out.mp3'; target.write_bytes(b'previous destination')
        with patch('workflow.audio_cache.write_json', side_effect=OSError('metadata write failed')):
            with self.assertRaises(OSError):
                await speech('same', {}, target, self.produce)
        self.assertEqual((self.folder/'audio.mp3').read_bytes(), b'old uncommitted audio')
        self.assertEqual(target.read_bytes(), b'previous destination')
        self.assertFalse((self.folder/'receipt.json').exists())

    async def test_unbound_or_corrupt_pending_record_cannot_recover_audio(self):
        for value in ('wrong-identity', 'wrong-sha', 'missing-duration', 'invalid-duration', None, []):
            with self.subTest(value=value):
                self.folder.mkdir(parents=True, exist_ok=True)
                audio = self.folder/'audio.mp3'; audio.write_bytes(b'unbound audio')
                receipt = self.folder/'receipt.json'
                # Force each temporary case to exercise the pending record.
                if receipt.exists():
                    write_json(receipt, {'sha256': 'not-current'})
                record = {'cache_identity': self.identity, 'sha256': sha256(audio),
                          'duration_seconds': 1, 'created_at': 1}
                if value == 'wrong-identity': record['cache_identity'] = 'another-request'
                elif value == 'wrong-sha': record['sha256'] = 'another-audio'
                elif value == 'missing-duration': record.pop('duration_seconds')
                elif value == 'invalid-duration': record['duration_seconds'] = 0
                else: record = value
                write_json(self.folder/'pending_receipt.json', record)
                before = len(self.calls)
                self.assertFalse(await speech('same', {}, self.root/'out.mp3', self.produce))
                self.assertEqual(len(self.calls), before+1)
                self.assertEqual((self.root/'out.mp3').read_bytes(), b'completed audio')

    async def test_existing_valid_receipt_keeps_precedence_and_pending_evidence(self):
        self.folder.mkdir(parents=True)
        audio = self.folder/'audio.mp3'; audio.write_bytes(b'accepted cached audio')
        write_json(self.folder/'receipt.json', {'sha256': sha256(audio), 'duration_seconds': 1})
        pending = self.folder/'pending_receipt.json'
        write_json(pending, {'cache_identity': self.identity, 'sha256': 'interrupted-new-version'})
        original = pending.read_bytes()
        self.assertTrue(await speech('same', {}, self.root/'out.mp3', self.produce))
        self.assertEqual(self.calls, [])
        self.assertEqual((self.root/'out.mp3').read_bytes(), b'accepted cached audio')
        self.assertEqual(pending.read_bytes(), original)

    async def test_audio_publish_failure_does_not_recover_missing_audio(self):
        replace = os.replace
        def fail_audio(source, dest):
            if Path(dest) == self.folder/'audio.mp3':
                raise OSError('audio publish failed')
            return replace(source, dest)
        with patch('workflow.audio_cache.os.replace', side_effect=fail_audio):
            with self.assertRaises(OSError):
                await speech('same', {}, self.root/'first.mp3', self.produce)
        self.assertFalse((self.folder/'audio.mp3').exists())
        self.assertFalse((self.root/'first.mp3').exists())
        self.assertFalse(await speech('same', {}, self.root/'second.mp3', self.produce))
        self.assertEqual(len(self.calls), 2)
