import tempfile,unittest
from pathlib import Path
from workflow.caption_timing import events
from workflow.media import captions
class CaptionTests(unittest.TestCase):
    def test_short_tail_is_between_two_second_samples(self):
        p={'narration':[{'start':17.1,'end':21.8,'text':'两根主缆用吊索连接钢桁加劲梁。'}]}
        e=events(p);self.assertEqual(''.join(x['text'] for x in e),p['narration'][0]['text'])
        self.assertEqual(e[-1]['text'],'加劲梁。');self.assertTrue(20<e[-1]['start']<e[-1]['end']<22)
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'captions.ass';captions(p,f);self.assertIn('0:00:20.55,0:00:21.80',f.read_text())
