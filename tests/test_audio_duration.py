import shutil
import tempfile
import unittest
from pathlib import Path
from workflow import media

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
class AudioDurationTests(unittest.TestCase):
    def test_sparse_narration_is_finite_and_preserves_late_speech(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);tone=root/'tone.wav';out=root/'narration.wav'
            media.ff('-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=0.5',tone)
            p={'narration':[{'start':.2,'end':1.2,'text':'开头','audio_path':str(tone)},
                            {'start':27,'end':28,'text':'结尾','audio_path':str(tone)}]}
            result=media.narration(p,out)
            self.assertAlmostEqual(media.duration(out),30,places=2)
            self.assertLess(out.stat().st_size,6_000_000)
            self.assertGreater(media.audio_rms(out,27.05,.2),-35)
            self.assertLess(media.audio_rms(out,29,.2),-70)
            self.assertEqual(result['repair_rounds'],0)

    def test_deliberate_delivery_cannot_be_sped_up_to_fit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);tone=root/'tone.wav'
            media.ff('-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=1',tone)
            p={'narration':[{'start':26,'end':26.9,'text':'坚定收尾',
                            'audio_path':str(tone),'preserve_delivery':True}]}
            with self.assertRaisesRegex(ValueError,'longer slot'):
                media.narration(p,root/'out.wav')
