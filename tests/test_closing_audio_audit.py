import array
import unittest
from workflow.closing_audio import evaluate,RATE

class ClosingAuditTests(unittest.TestCase):
    def samples(self):
        mix=array.array('f',[.04])*(30*RATE)
        music=array.array('f',[.025])*(30*RATE)
        voice=array.array('f',[0])*(30*RATE)
        voice[25*RATE:int(27.8*RATE)]=array.array('f',[.1])*int(2.8*RATE)
        plan={'duration_seconds':30,'shots':[{'duration_seconds':30}], 'narration':[{'start':25,'end':28,'text':'ending'}]}
        return mix,voice,music,plan
    def test_complete_ending_passes_but_not_semantics(self):
        r=evaluate(*self.samples(),[{'preserve_delivery':True,'tempo':1}])
        self.assertTrue(r['programmatic_passed']);self.assertFalse(r['content_approved'])
    def test_missing_bed_and_late_voice_are_detected(self):
        mix,voice,music,plan=self.samples()
        music[26*RATE:27*RATE]=array.array('f',[0])*RATE
        voice[29*RATE:30*RATE]=array.array('f',[.1])*RATE
        r=evaluate(mix,voice,music,plan,[])
        self.assertFalse(r['checks']['music_under_entire_final_line'])
        self.assertFalse(r['checks']['measured_voice_ends_before_tail'])
    def test_accelerated_preserved_delivery_fails(self):
        r=evaluate(*self.samples(),[{'preserve_delivery':True,'tempo':1.1}])
        self.assertFalse(r['checks']['last_word_delivery_unaccelerated'])

    def test_tiny_remainder_uses_full_energy_window(self):
        mix,voice,music,plan=self.samples()
        plan['narration'][0]['end']=28.002
        music[28*RATE:int(28.002*RATE)]=array.array('f',[0])*(int(28.002*RATE)-28*RATE)
        result=evaluate(mix,voice,music,plan,[])
        self.assertTrue(result['checks']['music_under_entire_final_line'])
        self.assertAlmostEqual(result['closing_windows'][-1]['end']-result['closing_windows'][-1]['start'],.25)
        music[int(27.752*RATE):int(28.002*RATE)]=array.array('f',[0])*(int(28.002*RATE)-int(27.752*RATE))
        self.assertFalse(evaluate(mix,voice,music,plan,[])['checks']['music_under_entire_final_line'])
