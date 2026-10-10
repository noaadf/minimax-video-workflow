import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow.tts_word_boundaries import save_with_timing


class Stream:
    def __init__(self, rows, fail=False):
        self.rows, self.fail = rows, fail

    async def stream(self):
        for row in self.rows:
            yield row
        if self.fail:
            raise RuntimeError("interrupted")


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_boundaries_are_hash_bound_to_saved_audio(self):
        rows = [{"type": "audio", "data": b"audio bytes"},
                {"type": "WordBoundary", "offset": 1_000_000, "duration": 8_000_000, "text": "你好"}]
        with tempfile.TemporaryDirectory() as d, patch("workflow.tts_word_boundaries.duration", return_value=2):
            audio, timing = Path(d) / "speech.mp3", Path(d) / "timing.json"
            result = await save_with_timing("你好。", {}, audio, timing, Stream(rows))
            self.assertEqual(audio.read_bytes(), b"audio bytes")
            self.assertEqual(result["source_path"], str(audio.resolve()))
            self.assertEqual(json.loads(timing.read_text())["words"][0]["start"], .1)
            self.assertEqual(list(Path(d).glob("*.part.*")), [])

    async def test_failed_stream_or_missing_cues_do_not_publish_partial_pair(self):
        for stream in [Stream([{"type": "audio", "data": b"partial"}], fail=True),
                       Stream([{"type": "audio", "data": b"audio"}])]:
            with tempfile.TemporaryDirectory() as d, patch("workflow.tts_word_boundaries.duration", return_value=2):
                with self.assertRaises((RuntimeError, ValueError)):
                    await save_with_timing("你好。", {}, Path(d) / "speech.mp3", Path(d) / "timing.json", stream)
                self.assertEqual(list(Path(d).iterdir()), [])
