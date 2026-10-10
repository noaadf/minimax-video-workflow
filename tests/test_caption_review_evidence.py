import json
import tempfile
import unittest
from pathlib import Path
from workflow.caption_timing import review_evidence
from workflow.plan import sha256

class CaptionReviewEvidenceTests(unittest.TestCase):
    def test_word_end_is_distinct_from_slot_and_asr_remains_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);audio=root/'voice';audio.write_bytes(b'unchanged')
            cue=root/'cue.json';cue.write_text(json.dumps({'schema_version':1,
                'method':'asr_estimate','script_text':'好。','source_path':str(audio),
                'source_sha256':sha256(audio),'source_seconds':2,
                'words':[{'start':0,'end':.7,'text':'好'}]}))
            line={'start':10,'end':12,'text':'好。','audio_path':str(audio),
                  'word_timing_path':str(cue)}
            result=review_evidence([line])[0]
            self.assertEqual(result['slot_end'],12)
            self.assertEqual(result['estimated_last_word_end'],10.7)
            self.assertEqual(result['trailing_slot_seconds'],1.3)
            self.assertFalse(result['listening_verified'])
            self.assertEqual(result['method'],'asr_estimate')
            audio.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'played narration audio'):
                review_evidence([line])

    def test_missing_cues_never_invent_spoken_end(self):
        result=review_evidence([{'start':0,'end':4,'text':'unknown'}])[0]
        self.assertEqual(result['status'],'unavailable')
        self.assertNotIn('estimated_last_word_end',result)
        self.assertTrue(result['slot_end_is_not_spoken_content_end'])
