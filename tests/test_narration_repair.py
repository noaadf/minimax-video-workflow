import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import media

class FakeTTS:
    def __init__(self,text,*args,**kwargs):self.text=text
    async def save(self,path):Path(path).write_text(self.text)

class NarrationRepairTests(unittest.TestCase):
    def test_only_oversized_line_rewritten_and_returned_for_captions(self):
        p={'facts':[],'narration':[{'start':0,'end':3,'text':'这段解说的文字实在太长无法在三秒之内读完。'},
                                  {'start':4,'end':7,'text':'稳稳向前。'}]}
        with tempfile.TemporaryDirectory() as d, patch.dict('sys.modules',{'edge_tts':types.SimpleNamespace(Communicate=FakeTTS)}), \
             patch.object(media,'duration',lambda p:len(Path(p).read_text())/3), patch.object(media,'ff'), \
             patch('workflow.writing.shorten',return_value=([{'index':0,'text':'向高处！'}],{'model':'fake'})) as short:
            result=media.narration(p,Path(d)/'voice.wav')
            self.assertEqual(result['repair_rounds'],1)
            self.assertEqual(result['effective_narration'][0]['text'],'向高处！')
            self.assertEqual(result['effective_narration'][1]['text'],'稳稳向前。')
            self.assertEqual(short.call_args.args[1][0]['index'],0)
            self.assertNotEqual(p['narration'][0]['text'],'向高处！')
    def test_bounded_rewrites(self):
        long='这段解说的文字实在太长无法在三秒之内读完。'
        p={'facts':[],'narration':[{'start':0,'end':3,'text':long}]}
        with tempfile.TemporaryDirectory() as d, patch.dict('sys.modules',{'edge_tts':types.SimpleNamespace(Communicate=FakeTTS)}), \
             patch.object(media,'duration',lambda p:len(Path(p).read_text())/3), patch.object(media,'ff'), \
             patch('workflow.writing.shorten',return_value=([{'index':0,'text':long}],{})) as short:
            with self.assertRaisesRegex(ValueError,'two bounded'):media.narration(p,Path(d)/'voice.wav')
            self.assertEqual(short.call_count,2)
    def test_supplied_audio_not_silently_rewritten(self):
        with tempfile.TemporaryDirectory() as d:
            audio=Path(d)/'old.mp3';audio.write_text('existing voice')
            p={'facts':[],'narration':[{'start':0,'end':1,'text':'台词','audio_path':str(audio)}]}
            with patch.object(media,'duration',return_value=10),patch('workflow.writing.shorten') as short:
                with self.assertRaisesRegex(ValueError,'supplied narration'):media.narration(p,Path(d)/'voice.wav')
                short.assert_not_called()

if __name__=='__main__':unittest.main()

class DeliberateEndingRepairTests(unittest.TestCase):
    def test_generated_deliberate_ending_shortens_without_acceleration(self):
        p={'facts':[],'narration':[{'start':20,'end':25,'text':'这是一句需要缓慢而坚定地说出的特别漫长的结束语。','preserve_delivery':True,'voice':{'rate':'-14%'}}]}
        with tempfile.TemporaryDirectory() as d, patch.dict('sys.modules',{'edge_tts':types.SimpleNamespace(Communicate=FakeTTS)}), patch.object(media,'duration',lambda p:len(Path(p).read_text())/3), patch.object(media,'ff'), patch('workflow.writing.shorten',return_value=([{'index':0,'text':'把远方，接近日常。'}],{})):
            result=media.narration(p,Path(d)/'voice.wav')
            self.assertEqual(result['segments'][0]['tempo'],1)
            self.assertEqual(result['segments'][0]['voice']['rate'],'-14%')
            self.assertEqual(result['repair_rounds'],1)
