"""Idempotent cloud TTS on existing CPU runtime; no accelerator allocations."""
import asyncio,fcntl,hashlib,json,math,os,shlex,subprocess,sys,time
from pathlib import Path

def identity(request):
 return hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def sha(path):
 return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def serve(request,root):
 import edge_tts
 from .cloud_dns import install
 from .core.io import write_json
 if set(request)!={'text','voice'} or not isinstance(request['text'],str) or not 0<len(request['text'])<=5000:raise ValueError('invalid speech request')
 folder=Path(root)/identity(request);folder.mkdir(parents=True,exist_ok=True)
 result=folder/'result.json';audio=folder/'speech.mp3';state=folder/'state.json'
 with (folder/'lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if result.exists():
   r=json.loads(result.read_text(encoding="utf-8"))
   if audio.is_file() and sha(audio)==r['sha256']:return r
   raise RuntimeError('completed remote speech artifact changed')
  if state.exists():raise RuntimeError('previous speech attempt incomplete/failed; requires diagnosis')
  write_json(state,{'status':'running','at':time.time()})
  try:
   install('speech.platform.bing.com',Path(root)/'speech_dns.json')
   v=request['voice'];temp=folder/'speech.part.mp3'
   asyncio.run(edge_tts.Communicate(request['text'],v.get('name','zh-CN-YunjianNeural'),rate=v.get('rate','+0%'),pitch=v.get('pitch','-18Hz'),volume=v.get('volume','+0%')).save(str(temp)))
   if temp.stat().st_size<512:raise ValueError('empty cloud speech')
   os.replace(temp,audio);r={'path':str(audio),'sha256':sha(audio),'bytes':audio.stat().st_size,'request_id':identity(request)}
   write_json(result,r);write_json(state,{'status':'succeeded','at':time.time()});return r
  except Exception as e:
   write_json(state,{'status':'failed','error_type':type(e).__name__,'at':time.time()});raise

def synthesize(text,voice,destination,config=None):
 if isinstance(voice,dict) and voice.get('engine')=='minimax':
  from .minimax_tts import synthesize_minimax
  return synthesize_minimax(text,voice,destination)
 from .media import duration
 cfg=config or json.loads((Path(__file__).parent/'config/cloud_speech.json').read_text(encoding="utf-8"))
 req={'text':text,'voice':voice};rid=identity(req)
 options=['-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=20','-i',str(Path(cfg['identity_file']).expanduser())]
 command='cd '+shlex.quote(cfg['code_root'])+' && '+shlex.join([cfg['python'],'-m','workflow.cloud_speech',cfg['state_root']])
 response=subprocess.run(['ssh',*options,'-p',str(cfg['port']),cfg['host'],command],input=json.dumps(req).encode(),capture_output=True,timeout=180)
 if response.returncode:raise RuntimeError('cloud speech failed; inspect remote request '+rid)
 r=json.loads(response.stdout);expected=cfg['state_root'].rstrip('/')+'/'+rid+'/speech.mp3'
 if r['request_id']!=rid or r['path']!=expected:raise ValueError('invalid remote speech receipt')
 import uuid
 destination=Path(destination)
 temp=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.part.mp3')
 try:
  p=subprocess.run(['scp',*options,'-P',str(cfg['port']),cfg['host']+':'+expected,str(temp)],capture_output=True,timeout=60)
  if p.returncode or sha(temp)!=r['sha256']:raise RuntimeError('cloud speech transfer/hash failed')
  seconds=duration(temp)
  if not math.isfinite(seconds) or seconds<=0:raise ValueError('invalid cloud speech duration')
  os.replace(temp,destination)
  return r
 finally:temp.unlink(missing_ok=True)

def synthesize_timed(text,voice,destination,timing_destination,config=None):
 """Stage an immutable worker on the existing SSH CPU runtime; no service replacement."""
 if isinstance(voice,dict) and voice.get('engine')=='minimax':
  from .minimax_tts import synthesize_minimax_timed
  return synthesize_minimax_timed(text,voice,destination,timing_destination)
 import uuid
 from .media import duration
 from .core.io import write_json
 from .speech_timing import align_script,edge_words
 cfg=config or json.loads((Path(__file__).parent/'config/cloud_speech.json').read_text(encoding="utf-8"))
 audio=Path(destination).resolve();timing=Path(timing_destination).resolve()
 if audio==timing or audio.exists() or timing.exists():raise ValueError('timed cloud speech needs new output paths')
 audio.parent.mkdir(parents=True,exist_ok=True);timing.parent.mkdir(parents=True,exist_ok=True)
 req={'text':text,'voice':voice,'boundary':'WordBoundary'};rid=identity(req)
 options=['-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=20','-i',str(Path(cfg['identity_file']).expanduser())]
 worker=Path(__file__).with_name('timed_speech_worker.py')
 remote_dir=cfg['code_root'].rstrip('/')+'/timed_speech_v1';remote_worker=remote_dir+'/'+sha(worker)+'.py'
 def ssh(command,**kwargs):
  result=subprocess.run(['ssh',*options,'-p',str(cfg['port']),cfg['host'],command],capture_output=True,timeout=180,**kwargs)
  if result.returncode:raise RuntimeError('timed cloud speech failed; inspect remote request '+rid)
  return result
 def fetch(remote,local):
  r=subprocess.run(['scp',*options,'-P',str(cfg['port']),cfg['host']+':'+remote,str(local)],capture_output=True,timeout=60)
  if r.returncode:raise RuntimeError('timed cloud speech transfer failed')
 ssh(shlex.join(['mkdir','-p',remote_dir]))
 remote_temp=remote_dir+'/'+uuid.uuid4().hex+'.part.py'
 uploaded=subprocess.run(['scp',*options,'-P',str(cfg['port']),str(worker),cfg['host']+':'+remote_temp],capture_output=True,timeout=60)
 if uploaded.returncode:raise RuntimeError('timed speech worker transfer failed')
 # Atomic rename: concurrent identical uploads cannot expose a partially written worker.
 ssh(shlex.join(['mv',remote_temp,remote_worker]))
 command='cd '+shlex.quote(cfg['code_root'])+' && '+shlex.join([cfg['python'],remote_worker,cfg['state_root']])
 response=ssh(command,input=json.dumps(req).encode())
 r=json.loads(response.stdout);base=cfg['state_root'].rstrip('/')+'/'+rid
 if r.get('request_id')!=rid or r.get('path')!=base+'/speech.mp3' or r.get('words_path')!=base+'/words.json':
  raise ValueError('invalid timed cloud speech receipt')
 temp_audio=audio.with_name(uuid.uuid4().hex+'.part.mp3');temp_words=timing.with_name(uuid.uuid4().hex+'.part.json')
 publishing=False
 try:
  fetch(r['path'],temp_audio);fetch(r['words_path'],temp_words)
  if sha(temp_audio)!=r.get('sha256') or sha(temp_words)!=r.get('words_sha256'):
   raise ValueError('timed cloud speech artifact hash differs')
  raw=json.loads(temp_words.read_text(encoding="utf-8"))
  if raw.get('script_text')!=text or raw.get('audio_sha256')!=sha(temp_audio):raise ValueError('timed cloud script/audio differs')
  evidence={'schema_version':1,'method':'edge_word_boundary','script_text':text,
      'source_path':str(temp_audio),'source_sha256':sha(temp_audio),'source_seconds':duration(temp_audio),
      'words':edge_words(raw['boundaries']),'raw_boundaries':raw['boundaries'],'voice':voice,'listening_verified':False}
  align_script(text,evidence,minimum_match=1)
  evidence['source_path']=str(audio)
  write_json(temp_words,evidence)
  # Exclusive publication protects outputs created while transport was in flight.
  # A failed pair commit retains both verified staging files for diagnosis.
  publishing=True
  os.link(temp_audio,audio);os.link(temp_words,timing)
  publishing=False
  return r
 finally:
  if not publishing:
   temp_audio.unlink(missing_ok=True);temp_words.unlink(missing_ok=True)

if __name__=='__main__':
 print(json.dumps(serve(json.load(sys.stdin),sys.argv[1])))
