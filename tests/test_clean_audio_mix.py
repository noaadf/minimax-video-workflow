import json,shutil,tempfile,unittest
from pathlib import Path
import numpy as np
from workflow import media
from workflow.plan import sha256

@unittest.skipUnless(shutil.which('ffmpeg'),'ffmpeg required')
class CleanAudioMix(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  for name,freq in [('voice',220),('music',440),('native',660)]:
   media.ff('-f','lavfi','-i',f'sine=frequency={freq}:duration=3:sample_rate=48000','-ac','2',self.root/(name+'.wav'))
  media.ff('-f','lavfi','-i','color=c=blue:s=320x568:r=24:d=3','-f','lavfi','-i','sine=frequency=900:duration=3:sample_rate=48000','-c:v','libx264','-preset','ultrafast','-c:a','aac',self.root/'shot.mp4')
  self.plan={'duration_seconds':3,'cover_title':'测试','narration':[{'start':0,'end':1,'text':'声音测试'}],'use_native_foley':True,'sound_effects':[{'path':str(self.root/'native.wav'),'origin':'native_foley','start':0,'duration_seconds':3,'gain_db':0}]}
 def test_legacy_native_audio_cannot_leak_into_final_mix(self):
  out=self.root/'out';out.mkdir();result=media.compose(self.plan,[self.root/'shot.mp4'],self.root/'voice.wav',self.root/'music.wav',out)
  raw=media.run('ffmpeg','-v','error','-ss','0.5','-i',result['video_path'],'-t','1','-vn','-ac','1','-ar','48000','-f','f32le','pipe:1')
  pcm=np.frombuffer(raw,dtype='<f4');spectrum=np.abs(np.fft.rfft(pcm*np.hanning(len(pcm))));freqs=np.fft.rfftfreq(len(pcm),1/48000)
  def peak(f):return spectrum[np.abs(freqs-f)<3].max()
  self.assertGreater(peak(220),1);self.assertGreater(peak(440),1)
  self.assertLess(peak(660)/peak(220),.01);self.assertLess(peak(900)/peak(220),.01)
  receipt=json.loads(Path(result['audio_mix_path']).read_text());self.assertFalse(receipt['source_audio_in_mix']);self.assertEqual(receipt['curated_effects'],[])
 def test_curated_effect_requires_explicit_allow_speech_review_and_hash(self):
  fx={'path':str(self.root/'native.wav'),'origin':'curated_sfx','speech_free_verified':True,'source_sha256':sha256(self.root/'native.wav')}
  plan={'sound_effects':[fx]};self.assertEqual(media.audio_effects(plan),[])
  plan['audio_policy']={'allow_curated_sfx':True};self.assertEqual(media.audio_effects(plan),[fx])
  fx['source_sha256']='bad'
  with self.assertRaises(ValueError):media.audio_effects(plan)
