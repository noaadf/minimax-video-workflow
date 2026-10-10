import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from workflow.caption_timing import events
from workflow.speech_timing import aligned_events, edge_words, semantic_chunks


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audio = Path(self.tmp.name) / "audio.mp3"
        self.audio.write_bytes(b"test audio")
        self.text = "车要合用，服务要跟上。"
        self.timing = {"schema_version": 1, "method": "edge_word_boundary", "script_text": self.text,
                       "source_path": str(self.audio), "source_sha256": hashlib.sha256(b"test audio").hexdigest(),
                       "source_seconds": 10, "words": [
                           {"start": 1, "end": 3, "text": "车要合用"},
                           {"start": 4, "end": 6.5, "text": "服务要跟上"}]}
        self.line = {"text": self.text, "start": 20, "end": 30}

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_word_endpoints_and_text_preserved(self):
        rows = aligned_events(self.line, self.timing, 0)
        self.assertEqual("".join(r["text"] for r in rows), self.text)
        self.assertEqual([(r["start"], r["end"]) for r in rows], [(21, 23), (24, 26.5)])

    def test_playback_tempo_and_source_trim(self):
        self.line.update(caption_tempo=2, caption_source_start=.5)
        rows = aligned_events(self.line, self.timing, 0)
        self.assertEqual([(r["start"], r["end"]) for r in rows], [(20.25, 21.25), (21.75, 23)])

    def test_asr_typo_never_changes_confirmed_words(self):
        self.timing["method"] = "asr_estimate"
        self.timing["words"][1]["text"] = "服务要跟赏"
        rows = aligned_events(self.line, self.timing, 0)
        self.assertEqual(rows[-1]["text"], "服务要跟上。")
        self.assertEqual(rows[-1]["timing_evidence"]["substitutions"], 1)
        self.assertFalse(rows[-1]["timing_evidence"]["listening_verified"])

    def test_changed_audio_rejects_stale_timing(self):
        self.audio.write_bytes(b"different recording")
        with self.assertRaisesRegex(ValueError, "hash changed"):
            aligned_events(self.line, self.timing, 0)

    def test_valid_cues_for_another_recording_do_not_match_played_audio(self):
        another = Path(self.tmp.name) / "other.mp3"
        another.write_bytes(b"other recording")
        self.line["audio_path"] = str(another)
        with self.assertRaisesRegex(ValueError, "played narration"):
            aligned_events(self.line, self.timing, 0)

    def test_traditional_asr_is_compared_without_rewriting_script(self):
        self.timing["method"] = "asr_estimate"
        self.timing["words"][0]["text"] = "車要合用"
        self.timing["words"][1]["text"] = "服務要跟上"
        rows = aligned_events(self.line, self.timing, 0)
        self.assertEqual("".join(row["text"] for row in rows), self.text)
        self.assertEqual(rows[0]["timing_evidence"]["exact_match_ratio"], 1)

    def test_invalid_cues_fail_closed(self):
        edits = [lambda t: t["words"][1].update(start=2),
                 lambda t: t["words"][1].update(end=11),
                 lambda t: t["words"][0].update(start=float("nan")),
                 lambda t: t["words"].clear(),
                 lambda t: t.update(script_text="different"),
                 lambda t: t["words"][0].update(text="毫不相关的句子")]
        for edit in edits:
            timing = copy.deepcopy(self.timing)
            edit(timing)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                aligned_events(self.line, timing, 0)

    def test_long_phrase_requires_authored_break_and_no_hard_cut(self):
        text = "这是一个需要按完整语义分句而不是机械地按照字数拆开的长句。"
        with self.assertRaisesRegex(ValueError, "authored semantic"):
            semantic_chunks(text)
        chunks = ["这是一个需要按完整语义分句", "而不是机械地按照字数拆开的长句。"]
        self.assertEqual(semantic_chunks(text, chunks), chunks)
        with self.assertRaisesRegex(ValueError, "exact confirmed"):
            semantic_chunks(text, ["漏字"])

    def test_missing_word_boundary_is_not_invented(self):
        self.timing["words"][0]["text"] = "要合用"
        with self.assertRaisesRegex(ValueError, "missing spoken cue"):
            aligned_events(self.line, self.timing, 0)

    def test_new_policy_requires_timing_for_every_line(self):
        with self.assertRaisesRegex(ValueError, "word_timing_path"):
            events({"caption_policy": "speech_aligned_v1", "narration": [self.line]})
        path = Path(self.tmp.name) / "timing.json"
        path.write_text(json.dumps(self.timing, ensure_ascii=False))
        self.line["word_timing_path"] = str(path)
        self.assertEqual(events({"caption_policy": "speech_aligned_v1", "narration": [self.line]})[0]["start"], 21)

    def test_edge_tick_conversion(self):
        self.assertEqual(edge_words([{"offset": 10_000_000, "duration": 5_000_000, "text": "车"}]),
                         [{"start": 1, "end": 1.5, "text": "车"}])

    def test_multiline_layout_is_semantic_and_text_conserving(self):
        self.text = "偃师厂家会按需求调整货箱和油箱容量。"
        self.timing.update(script_text=self.text,
                           words=[{"start": 1, "end": 8, "text": self.text}])
        self.line.update(text=self.text)
        with self.assertRaisesRegex(ValueError, "caption_lines"):
            aligned_events(self.line, self.timing, 0)
        self.line["caption_lines"] = [["偃师厂家会按需求", "调整货箱和油箱容量。"]]
        row = aligned_events(self.line, self.timing, 0)[0]
        self.assertEqual(row["text"], self.text)
        self.assertEqual(row["display_text"], "偃师厂家会按需求\n调整货箱和油箱容量。")
        self.line["caption_lines"] = [["偃师厂家会按需求", "漏字。"]]
        with self.assertRaisesRegex(ValueError, "preserve"):
            aligned_events(self.line, self.timing, 0)
