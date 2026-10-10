"""Find a continuous musical span using inexpensive quarter-second RMS measurements."""
import array,json,math,os
from pathlib import Path
from .core.io import sha256, write_json

def select(source,offset,length):
 from .media import run
 root=Path(os.environ.get('WORKFLOW_AUDIO_CACHE',str(Path(__file__).resolve().parents[1]/'output/production/audio_cache')))
 cache=root/'music_sources'/('v2_'+sha256(source)+'.json')
 if cache.exists():spans=json.loads(cache.read_text(encoding="utf-8"))['spans']
 else:
  samples=array.array('f');samples.frombytes(run('ffmpeg','-v','error','-i',source,'-vn','-ac','1','-ar','2000','-f','f32le','pipe:1'))
  spans=[];start=None
  for i in range(0,len(samples),500):
   chunk=samples[i:i+500];audible=sum(x*x for x in chunk)/max(1,len(chunk))>10**(-50/10)
   if audible and start is None:start=i/2000
   if not audible and start is not None:
    if i/2000-start>=.5:spans.append([start,i/2000])
    start=None
  if start is not None and len(samples)/2000-start>=.5:spans.append([start,len(samples)/2000])
  write_json(cache,{'spans':spans,'threshold_db':-50,'window_seconds':.25})
 if math.isfinite(offset) and math.isfinite(length) and offset>=0 and length>0:
  for a,b in spans:
   if a<=offset and b-offset>=length:return offset,length
 raise ValueError(f'music window cannot satisfy requested offset={offset}, length={length}; '
                  f'available spans={spans}. Choose an explicit offset and length fully inside '
                  'one audible span, or supply a longer continuous music source; '
                  'the requested start and duration will not be changed automatically.')
