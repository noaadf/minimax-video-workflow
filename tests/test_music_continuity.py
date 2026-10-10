import unittest,tempfile,os
from pathlib import Path
from unittest.mock import patch
from workflow import media
class MusicContinuityTests(unittest.TestCase):
 def test_silent_tail_is_not_repeated_into_film(self):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':d}):
   root=Path(d);src=root/'source.wav';dest=root/'music.wav'
   media.ff('-f','lavfi','-i','sine=frequency=220:sample_rate=48000:duration=4','-af','apad=whole_dur=7','-t','7',src)
   plan={'duration_seconds':18,'narration':[{'start':1,'end':15}],'music':{'path':str(src)}}
   # Owner W2: a four-second span may no longer be looped to fill 18 seconds.
   with self.assertRaisesRegex(ValueError, r'offset=0, length=18.*available spans'):
    media.music(plan,dest)
   self.assertFalse(dest.exists())

 def test_exact_audible_window_preserves_mix_and_tail_fade(self):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':d}):
   root=Path(d);src=root/'source.wav';dest=root/'music.wav'
   media.ff('-f','lavfi','-i','sine=frequency=220:sample_rate=48000:duration=20',src)
   plan={'duration_seconds':18,'narration':[{'start':1,'end':15}],'music':{'path':str(src)}}
   media.music(plan,dest)
   self.assertAlmostEqual(media.duration(dest),18,places=2)
   for t in [3.5,4,6.5,7,10,13,15]:self.assertGreater(media.audio_rms(dest,t,.25),-40)
   self.assertLess(media.audio_rms(dest,17.8,.19),media.audio_rms(dest,16,.19)-10)
