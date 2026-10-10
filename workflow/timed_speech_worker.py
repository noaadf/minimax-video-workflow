"""Versioned CPU-only Edge stream worker; captures audio and word cues together."""
import asyncio
import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path

def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path, value):
    temp=Path(str(path)+'.part.json')
    temp.write_text(json.dumps(value,ensure_ascii=False), encoding="utf-8")
    os.replace(temp,path)

def serve(request, root):
    if (set(request) != {'text','voice','boundary'} or request['boundary'] != 'WordBoundary' or
            not isinstance(request['text'],str) or not 0 < len(request['text']) <= 5000 or
            not isinstance(request['voice'],dict)):
        raise ValueError('invalid timed speech request')
    rid=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    folder=Path(root)/rid;folder.mkdir(parents=True,exist_ok=True)
    result=folder/'result.json';audio=folder/'speech.mp3';words=folder/'words.json';state=folder/'state.json'
    with (folder/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if result.exists():
            r=json.loads(result.read_text(encoding="utf-8"))
            if audio.is_file() and words.is_file() and checksum(audio)==r['sha256'] and checksum(words)==r['words_sha256']:
                return r
            raise RuntimeError('completed timed speech artifact changed')
        if state.exists():
            raise RuntimeError('previous timed speech attempt incomplete; diagnose before retry')
        write(state,{'status':'running','at':time.time()})
        try:
            import edge_tts
            v=request['voice'];boundaries=[];temp=folder/'speech.part.mp3'
            async def capture():
                stream=edge_tts.Communicate(request['text'],v.get('name','zh-CN-YunjianNeural'),
                    rate=v.get('rate','+0%'),pitch=v.get('pitch','-18Hz'),volume=v.get('volume','+0%'),
                    boundary='WordBoundary')
                with temp.open('wb') as f:
                    async for chunk in stream.stream():
                        if chunk['type']=='audio':f.write(chunk['data'])
                        elif chunk['type']=='WordBoundary':
                            boundaries.append({k:chunk[k] for k in ('offset','duration','text')})
            asyncio.run(capture())
            if temp.stat().st_size < 512 or not boundaries:
                raise ValueError('timed speech has no audio or word boundaries')
            os.replace(temp,audio)
            write(words,{'script_text':request['text'],'audio_sha256':checksum(audio),'boundaries':boundaries})
            r={'path':str(audio),'sha256':checksum(audio),'words_path':str(words),
               'words_sha256':checksum(words),'request_id':rid}
            write(result,r);write(state,{'status':'succeeded','at':time.time()});return r
        except Exception as exc:
            write(state,{'status':'failed','error_type':type(exc).__name__,'at':time.time()});raise

if __name__=='__main__':
    # Existing cloud DNS helper, authenticated HTTPS, no global resolver change.
    sys.path.insert(0,os.getcwd())
    from workflow.cloud_dns import install
    root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True)
    install('speech.platform.bing.com',root/'speech_dns.json')
    print(json.dumps(serve(json.load(sys.stdin),root)))
