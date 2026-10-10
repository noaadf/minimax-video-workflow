import copy
import unittest
from workflow.voice_slot_repair import extend_into_pause


class TimingRepairTest(unittest.TestCase):
    def plan(self):
        return {'pacing_policy':'narrative_timed_v1','duration_seconds':7,
                'shots':[{'duration_seconds':3},{'duration_seconds':4}],
                'narration':[{'start':.25,'end':2.75,'text':'甲','audio_path':'a','preserve_delivery':True},
                             {'start':3.25,'end':4.75,'text':'乙','audio_path':'b','preserve_delivery':True}]}

    def test_pause_and_idempotency(self):
        p=self.plan(); old=copy.deepcopy(p)
        self.assertEqual(len(extend_into_pause(p,[2.5,1.5])),2)
        self.assertEqual(extend_into_pause(p,[2.5,1.5]),[])
        self.assertEqual(p['shots'],old['shots'])
        self.assertEqual([r['text'] for r in p['narration']],['甲','乙'])

    def test_no_cut_or_tail_overrun(self):
        p=self.plan();old=copy.deepcopy(p)
        self.assertEqual(extend_into_pause(p,[3,2]),[])
        self.assertEqual(p,old)

    def test_locked_audio_unchanged(self):
        p=self.plan();p['locked_audio']={'path':'approved.wav'};old=copy.deepcopy(p)
        self.assertEqual(extend_into_pause(p,[2.5,1.5]),[])
        self.assertEqual(p,old)
