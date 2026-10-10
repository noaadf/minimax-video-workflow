import json
import unittest
from unittest.mock import patch

from tests import test_audio_cache_commit_recovery as fixtures
from workflow.audio_cache import speech
from workflow.plan import digest, sha256, write_json


class AudioCacheValidityTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.AudioCacheCommitRecoveryTests.setUp
    produce = fixtures.AudioCacheCommitRecoveryTests.produce

    def cache(self, voice):
        identity = digest({'schema': 1, 'engine': 'edge-tts', 'text': 'same', 'voice': voice})
        folder = self.root/'speech'/identity; folder.mkdir(parents=True)
        return identity, folder

    async def test_invalid_synthesized_duration_cannot_publish_or_poison_cache(self):
        for i, seconds in enumerate((float('nan'), float('inf'), -float('inf'), 0, -1, True, None, '1')):
            with self.subTest(seconds=seconds):
                voice = {'case': i}; identity, folder = self.cache(voice)
                target = self.root/f'out-{i}.mp3'; target.write_bytes(b'previous destination')
                with patch('workflow.media.duration', return_value=seconds):
                    with self.assertRaises(ValueError):
                        await speech('same', voice, target, self.produce)
                self.assertEqual(target.read_bytes(), b'previous destination')
                for name in ('audio.mp3', 'receipt.json', 'pending_receipt.json'):
                    self.assertFalse((folder/name).exists(), name)
                self.assertEqual(list(folder.glob('*.part.mp3')), [])

    async def test_invalid_committed_receipt_is_a_cache_miss_without_type_crash(self):
        values = [None, [], True, 'invalid', {'missing': 'duration'}]
        values += [{'duration_seconds': value} for value in (float('nan'), float('inf'), 0, -1, True, '1')]
        values += [{'duration_seconds': 1, 'cache_identity': 'another-request'}]
        for i, value in enumerate(values):
            with self.subTest(receipt=value):
                voice = {'case': i}; identity, folder = self.cache(voice)
                audio = folder/'audio.mp3'; audio.write_bytes(b'old audio')
                if isinstance(value, dict):
                    value = {**value, 'sha256': sha256(audio)}
                write_json(folder/'receipt.json', value)
                before = len(self.calls)
                result = await speech('same', voice, self.root/f'out-{i}.mp3', self.produce)
                self.assertFalse(result)
                self.assertEqual(len(self.calls), before+1)
                self.assertEqual((self.root/f'out-{i}.mp3').read_bytes(), b'completed audio')
                repaired = json.loads((folder/'receipt.json').read_text())
                self.assertEqual(repaired['duration_seconds'], 1)
                self.assertEqual(repaired['cache_identity'], identity)

    async def test_valid_pending_commit_recovers_despite_invalid_main_receipt(self):
        for i, main in enumerate((None, {'duration_seconds': float('nan')})):
            with self.subTest(main=main):
                voice = {'case': i}; identity, folder = self.cache(voice)
                audio = folder/'audio.mp3'; audio.write_bytes(b'completed audio')
                if isinstance(main, dict):
                    main['sha256'] = sha256(audio)
                write_json(folder/'receipt.json', main)
                pending = {'cache_identity': identity, 'sha256': sha256(audio),
                           'duration_seconds': 1, 'created_at': 1}
                write_json(folder/'pending_receipt.json', pending)
                self.assertTrue(await speech('same', voice, self.root/f'out-{i}.mp3', self.produce))
                self.assertEqual(self.calls, [])
                self.assertEqual(self.probe.call_count, 0)
                self.assertEqual(json.loads((folder/'receipt.json').read_text()), pending)

    async def test_positive_legacy_receipts_without_identity_still_reuse_unchanged(self):
        for i, seconds in enumerate((1, 1.25)):
            with self.subTest(seconds=seconds):
                voice = {'case': i}; identity, folder = self.cache(voice)
                audio = folder/'audio.mp3'; audio.write_bytes(b'legacy audio')
                receipt = folder/'receipt.json'
                write_json(receipt, {'sha256': sha256(audio), 'duration_seconds': seconds})
                original = receipt.read_bytes()
                self.assertTrue(await speech('same', voice, self.root/f'out-{i}.mp3', self.produce))
                self.assertEqual(self.calls, [])
                self.assertEqual(self.probe.call_count, 0)
                self.assertEqual(receipt.read_bytes(), original)
