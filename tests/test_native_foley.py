import shutil
import tempfile
import types
import unittest
from pathlib import Path

from workflow import media
from workflow.runner import execute


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class NativeFoleyTests(unittest.TestCase):
    def test_shot_opt_out_blocks_embedded_and_explicit_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.mp4'
            media.ff('-f', 'lavfi', '-i', 'color=black:s=108x192:d=0.25',
                     '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.25',
                     '-shortest', '-c:v', 'libx264', '-c:a', 'aac', source)
            queue = types.SimpleNamespace(results=lambda deps: {})
            for explicit in (False, True):
                shot = {'id': 'S1', 'source_path': str(source), 'duration_seconds': 0.25,
                        'use_native_foley': False}
                if explicit:
                    shot['foley_source_path'] = str(root / 'must_not_be_read.wav')
                task = {'id': 'test:S1', 'stage': 'shot', 'deps': [], 'token': str(explicit),
                        'payload': {'folder': str(root), 'data': shot,
                                    'plan': {'input_sha256': {}, 'use_native_foley': True}}}
                with self.subTest(explicit=explicit):
                    result = execute(queue, task)
                    self.assertNotIn('foley_path', result)
                    self.assertTrue(Path(result['video_path']).is_file())
            shot.pop('use_native_foley')
            shot.pop('foley_source_path')
            task['token'] = 'inherited'
            result = execute(queue, task)
            self.assertIn('foley_path', result)
            self.assertGreater(media.audio_rms(result['foley_path'], 0, 0.2), -40)
