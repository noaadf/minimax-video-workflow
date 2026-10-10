import tempfile,unittest,shutil,json
from pathlib import Path
from unittest.mock import patch
from workflow import media
from workflow.plan import validate
class VariableDurationTests(unittest.TestCase):
 @unittest.skipUnless(shutil.which('ffmpeg'),'requires FFmpeg')
 def test_short_and_long_audio_and_caption_tails(self):
  with tempfile.TemporaryDirectory() as d, patch.dict('os.environ',{'WORKFLOW_AUDIO_CACHE':d}):
   root=Path(d);tone=root/'tone.wav';music=root/'music_source.wav'
   media.ff('-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=1',tone)
   # Music must cover the complete requested window; narration stays one second.
   media.ff('-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=46',music)
   for length in (18,45):
    p={'duration_seconds':length,'cover_title':'测试','music':{'path':str(music),'offset_seconds':.25},'narration':[{'start':length-4,'end':length-2,'text':'坚定收尾','audio_path':str(tone)}]}
    voice=root/f'voice{length}.wav';bed=root/f'bed{length}.wav';ass=root/'captions.ass'
    media.narration(p,voice);media.music(p,bed);media.captions(p,ass,cover=True)
    self.assertAlmostEqual(media.duration(voice),length,places=2);self.assertAlmostEqual(media.duration(bed),length,places=2)
    self.assertGreater(media.audio_rms(voice,length-3.9,.2),-35)
    self.assertIn(media.timestamp(length)+',Title',ass.read_text())
 def test_manifest_total_and_tail(self):
  p=json.loads(Path('output/production/pilot_six_20261001/pilot6_bridge_a_20261001.json').read_text()) if Path('output/production/pilot_six_20261001/pilot6_bridge_a_20261001.json').exists() else None
  if p is None:self.skipTest('fixture unavailable')
  p['duration_seconds']=35;p['shots'].append({**p['shots'][-1],'id':'EXTRA','duration_seconds':5});validate(p)
  p['narration'][-1]['end']=34.8
  with self.assertRaises(ValueError):validate(p)
