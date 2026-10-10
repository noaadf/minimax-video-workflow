import array
import tempfile
import unittest
from pathlib import Path

from workflow import media
from workflow.temporal_qc import scan, parse_frames, classify, audio_presence


class TemporalTests(unittest.TestCase):
    def test_missing_measurements_fail_closed(self):
        with self.assertRaises(ValueError):
            parse_frames('frame:0 pts:0 pts_time:0\nlavfi.scd.score=0')

    def test_declared_cut_is_not_an_error_and_static_is_informational(self):
        rows=[{'time':i/24,'YDIF':0,'YHIGH':80,'YLOW':50,'score':50 if i==48 else 0}
              for i in range(96)]
        r=classify(rows,4,[2],24)
        self.assertEqual(r['exceptions'],[])
        self.assertTrue(r['static_intervals'])
        r=classify(rows,4,[],24)
        self.assertEqual(r['exceptions'][0]['kind'],'unplanned_scene_change')

    def test_silent_planned_voice_is_detected(self):
        p={'narration':[{'start':.1,'end':.9,'text':'test'}]}
        self.assertFalse(audio_presence(array.array('f',[0])*16000,p)[0]['voice_energy_present'])
        self.assertTrue(audio_presence(array.array('f',[.1])*16000,p)[0]['voice_energy_present'])

    def test_sampled_pass_cannot_clear_programmatic_audio_exception(self):
        from workflow.temporal_qc import retain_exceptions
        r=retain_exceptions({'status':'automatic_review_passed','review':{'verdict':'pass','findings':[]}},
            {'exceptions':[{'kind':'decoded_peak_above_full_scale','start':2.5,'reason':'PCM overfull'}]},
            {'shots':[{'id':'S1','duration_seconds':2},{'id':'S2','duration_seconds':4}]})
        self.assertEqual(r['status'],'review_required')
        self.assertEqual(r['review']['findings'][0]['shot_ids'],['S2'])
        self.assertEqual(r['review']['findings'][0]['category'],'audio')

    def test_full_frame_black_flash_and_cache_are_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);video=root/'v.mp4'
            media.ff('-f','lavfi','-i','testsrc2=s=96x160:r=24:d=4',
                     '-f','lavfi','-i','sine=frequency=440:duration=4',
                     '-vf',"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='eq(n,35)'",
                     '-c:v','libx264','-c:a','aac','-shortest',video)
            plan={'shots':[{'duration_seconds':4}],'narration':[]}
            r=scan(video,root/'audit',plan)
            self.assertEqual(r['frames_scanned'],96)
            self.assertTrue(r['technical_scan_passed'])
            black=[x for x in r['exceptions'] if x['kind']=='near_black_region']
            self.assertTrue(any(abs(x['start']-35/24)<.001 for x in black))
            self.assertFalse(r['content_approved']); self.assertEqual(r['api_calls'],0)
            self.assertEqual(scan(video,root/'audit',plan),r)
            r2=scan(video,root/'audit',plan,intentional_cuts=[1.5])
            self.assertNotEqual(r['input_digest'],r2['input_digest'])
