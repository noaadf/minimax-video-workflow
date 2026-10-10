"""Exercise publication races and interrupted local TTS with dummy stream bytes."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import tts_word_boundaries as adapter
from workflow.speech_timing import sha256


class LocalTimedPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, root, collision=None, interrupt=False, bad_duration=False, stream_failure=False):
        audio=root/'voice.mp3';timing=root/'words.json';calls=[]
        class Stream:
            async def stream(self):
                calls.append(1)
                yield {'type':'audio','data':b'accepted_audio'}
                if collision=='audio':audio.write_bytes(b'other_audio')
                if collision=='timing':timing.write_bytes(b'other_evidence')
                if stream_failure:raise RuntimeError('stream interrupted')
                yield {'type':'WordBoundary','offset':0,'duration':10000000,'text':'你好'}
        link=adapter.os.link;replace=adapter.os.replace
        def publication(fn):
            def call(src,dst,*args,**kwargs):
                if interrupt and Path(dst)==timing:raise OSError('timing publication interrupted')
                return fn(src,dst,*args,**kwargs)
            return call
        with patch.object(adapter,'duration',return_value=float('nan') if bad_duration else 1.2),patch.object(adapter.os,'link',side_effect=publication(link)),patch.object(adapter.os,'replace',side_effect=publication(replace)):
            result=await adapter.save_with_timing('你好。',{},audio,timing,Stream())
        self.assertEqual(calls,[1])
        return result

    def assert_retained(self,root):
        audios=list(root.glob('*.part.mp3'));words=list(root.glob('*.part.json'))
        self.assertEqual(len(audios),1);self.assertEqual(len(words),1)
        evidence=json.loads(words[0].read_text())
        self.assertEqual(evidence['source_sha256'],sha256(audios[0]))
        self.assertEqual(evidence['source_path'],str(root/'voice.mp3'))
        self.assertEqual(evidence['script_text'],'你好。')
        self.assertFalse(evidence['listening_verified'])

    async def test_audio_created_during_stream_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            with self.assertRaises(FileExistsError):await self.exercise(root,collision='audio')
            self.assertEqual((root/'voice.mp3').read_bytes(),b'other_audio')
            self.assertFalse((root/'words.json').exists());self.assert_retained(root)

    async def test_timing_created_during_stream_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            with self.assertRaises(FileExistsError):await self.exercise(root,collision='timing')
            self.assertEqual((root/'words.json').read_bytes(),b'other_evidence')
            self.assertEqual((root/'voice.mp3').read_bytes(),b'accepted_audio');self.assert_retained(root)

    async def test_second_publication_failure_retains_pair_and_blocks_replay(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            with self.assertRaisesRegex(OSError,'timing publication interrupted'):
                await self.exercise(root,interrupt=True)
            self.assertEqual((root/'voice.mp3').read_bytes(),b'accepted_audio')
            self.assertFalse((root/'words.json').exists());self.assert_retained(root)
            class ForbiddenStream:
                async def stream(self):
                    raise AssertionError('must not replay synthesis')
                    yield
            with self.assertRaisesRegex(ValueError,'new output paths'):
                await adapter.save_with_timing('你好。',{},root/'voice.mp3',root/'words.json',ForbiddenStream())
            self.assert_retained(root)

    async def test_success_binds_exact_pair_without_leftover_staging(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();result=await self.exercise(root)
            self.assertEqual(result,json.loads((root/'words.json').read_text()))
            self.assertEqual(result['source_sha256'],sha256(root/'voice.mp3'))
            self.assertEqual(result['source_path'],str(root/'voice.mp3'))
            self.assertEqual(sorted(p.name for p in root.iterdir()),['voice.mp3','words.json'])

    async def test_invalid_duration_never_publishes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            with self.assertRaisesRegex(ValueError,'source duration'):await self.exercise(root,bad_duration=True)
            self.assertEqual(list(root.iterdir()),[])

    async def test_stream_failure_never_publishes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            with self.assertRaisesRegex(RuntimeError,'stream interrupted'):await self.exercise(root,stream_failure=True)
            self.assertEqual(list(root.iterdir()),[])

if __name__=='__main__':unittest.main()
