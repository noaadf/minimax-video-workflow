import asyncio,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from workflow.audio_cache import speech

class SpeechCacheTests(unittest.IsolatedAsyncioTestCase):
 async def test_concurrent_identical_requests_synthesize_once(self):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':d}),patch('workflow.media.duration',return_value=1):
   calls=[]
   async def produce(p):calls.append(p);await asyncio.sleep(.02);p.write_bytes(b'audio')
   a,b=Path(d)/'a.mp3',Path(d)/'b.mp3'
   results=await asyncio.gather(speech('same',{},a,produce),speech('same',{},b,produce))
   self.assertEqual(len(calls),1);self.assertEqual(sorted(results),[False,True]);self.assertEqual(a.read_bytes(),b.read_bytes())
 async def test_failed_producer_not_cached_and_voice_changes_invalidate(self):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':d}),patch('workflow.media.duration',return_value=1):
   async def fail(p):p.write_bytes(b'partial');raise RuntimeError('network failure')
   with self.assertRaises(RuntimeError):await speech('same',{},Path(d)/'a.mp3',fail)
   self.assertEqual(list(Path(d).rglob('receipt.json')),[])
   calls=[]
   async def produce(p):calls.append(p);p.write_bytes(b'audio')
   for voice in ({'rate':'+0%'},{'rate':'-10%'}):await speech('same',voice,Path(d)/'a.mp3',produce)
   self.assertEqual(len(calls),2)
 async def test_corrupt_cache_is_not_reused(self):
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':d}),patch('workflow.media.duration',return_value=1):
   async def produce(p):p.write_bytes(b'audio')
   await speech('same',{},Path(d)/'a.mp3',produce)
   next(Path(d).rglob('audio.mp3')).write_bytes(b'corrupt')
   self.assertFalse(await speech('same',{},Path(d)/'b.mp3',produce))
   self.assertEqual((Path(d)/'b.mp3').read_bytes(),b'audio')

class MusicCacheTests(unittest.TestCase):
 def test_reuse_and_end_timing_changes_invalidate(self):
  from workflow.media import music
  from workflow.plan import write_json
  with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'WORKFLOW_AUDIO_CACHE':d}),patch('workflow.media.duration',return_value=30):
   root=Path(d);src=root/'source.wav';src.write_bytes(b'original')
   p={'music':{'path':str(src)},'duration_seconds':30,'narration':[{'end':28}]}
   calls=[]
   def render(plan,dest):
    calls.append(plan);Path(dest).write_bytes(b'rendered');write_json(Path(dest).with_suffix('.selection.json'),{'offset_seconds':0,'span_seconds':30})
   with patch('workflow.media._render_music',side_effect=render):
    music(p,root/'a.wav');music(p,root/'b.wav');self.assertEqual(len(calls),1)
    p['narration'][0]['end']=27;music(p,root/'c.wav');self.assertEqual(len(calls),2)
