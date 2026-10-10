import tempfile
import unittest
from pathlib import Path
from scripts.render_timeline_candidates import render,validate
from workflow import media
from workflow.plan import sha256

class TimelineOffsetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.src=self.root/'source.mp4'
        media.ff('-f','lavfi','-i','color=red:s=108x192:r=24:d=1','-f','lavfi','-i','color=blue:s=108x192:r=24:d=1',
                 '-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','libx264','-pix_fmt','yuv420p',self.src)
        self.spec={'fps':24,'inputs':{'s':{'path':str(self.src),'sha256':sha256(self.src)}},
                   'outputs':{'clip':{'parent':'test','shot':'S7','frames':24,'segments':[{'input':'s','start_frame':24,'frames':24}]}}}
    def test_reads_continuation_not_repeated_first_second(self):
        result=render(self.spec,self.root/'out');path=result['outputs']['clip']['path']
        rgb=media.run('ffmpeg','-v','error','-i',path,'-frames:v','1','-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','-')
        self.assertGreater(rgb[2],200);self.assertLess(rgb[0],20)
        self.assertEqual(result['outputs']['clip']['frames'],24)
        stream=media.probe(path)['streams'][0]
        self.assertEqual(stream['avg_frame_rate'],'24/1')
        self.assertEqual(stream['time_base'],'1/12288')
        self.assertEqual(float(stream['duration']),1)
    def test_negative_or_noninteger_offset_rejected(self):
        for offset in [-1,.5,True]:
            self.spec['outputs']['clip']['segments'][0]['start_frame']=offset
            with self.assertRaises(ValueError):validate(self.spec)
    def test_offset_past_source_does_not_pad_or_silently_restart(self):
        self.spec['outputs']['clip']['segments'][0]['start_frame']=48
        with self.assertRaisesRegex(ValueError,'including offset'):render(self.spec,self.root/'out')

    def test_audio_cannot_hide_truncated_video(self):
        from workflow.programmatic_qc import screen_video
        from workflow.temporal_qc import scan
        broken=self.root/'short_video_long_audio.mp4'
        media.ff('-f','lavfi','-i','color=red:s=108x192:r=24:d=1','-f','lavfi','-i','anullsrc=r=48000:cl=stereo:d=2',
                 '-map','0:v','-map','1:a','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',broken)
        plan={'duration_seconds':2,'shots':[{'id':'S1','duration_seconds':2}],'narration':[]}
        report=screen_video(broken,self.root/'screen',plan)
        self.assertTrue(report['checks']['duration'])
        self.assertFalse(report['checks']['video_duration']);self.assertTrue(report['hard_failed'])
        temporal=scan(broken,self.root/'temporal',plan)
        self.assertFalse(temporal['technical_scan_passed'])
        self.assertFalse(temporal['decoded_timeline_complete'])

    def test_adjacent_replay_requires_explicit_intent(self):
        self.spec['outputs']={'a':{'parent':'same','shot':'S6','global_start':0,'frames':24,'segments':[{'input':'s','frames':24}]},
                              'b':{'parent':'same','shot':'S7','global_start':1,'frames':24,'segments':[{'input':'s','frames':24}]}}
        with self.assertRaisesRegex(ValueError,'adjacent source restart'):validate(self.spec)
        self.spec['outputs']['b']['segments'][0]['start_frame']=24;validate(self.spec)
        self.spec['outputs']['b']['segments'][0].update(start_frame=0,intentional_repeat=True);validate(self.spec)
