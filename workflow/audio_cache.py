"""Content-addressed speech cache shared by CPU workers; no failed/partial audio reuse."""
import asyncio
import fcntl
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from .core.io import digest, sha256, write_json

def _valid_duration(seconds):
    return type(seconds) in (int,float) and 0<seconds<float('inf')


async def speech(text, voice, destination, producer):
    from .media import duration
    root=Path(os.environ.get('WORKFLOW_AUDIO_CACHE',str(Path(__file__).resolve().parents[1]/'output/production/audio_cache')))
    identity=digest({'schema':1,'engine':'edge-tts','text':text,'voice':voice})
    folder=root/'speech'/identity
    folder.mkdir(parents=True,exist_ok=True)
    audio=folder/'audio.mp3';receipt=folder/'receipt.json';destination=Path(destination)
    pending=folder/'pending_receipt.json'
    started=time.monotonic()
    with (folder/'write.lock').open('a') as lock:
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()-started>180:raise TimeoutError('speech cache writer timeout')
                await asyncio.sleep(.1)
        reused=False
        if receipt.exists() and audio.exists():
            try:
                record=json.loads(receipt.read_text(encoding="utf-8"))
                reused=(isinstance(record,dict) and _valid_duration(record.get('duration_seconds'))
                        and record.get('cache_identity',identity)==identity
                        and record.get('sha256')==sha256(audio))
            except (KeyError,ValueError):pass
        # Finish an interrupted two-file commit without synthesizing the same text again.
        if not reused and pending.exists() and audio.exists():
            try:
                record=json.loads(pending.read_text(encoding="utf-8"))
                reused=(isinstance(record,dict) and record.get('cache_identity')==identity
                        and _valid_duration(record.get('duration_seconds'))
                        and record.get('sha256')==sha256(audio))
                if reused:os.replace(pending,receipt)
            except (KeyError,ValueError):pass
        if not reused:
            temp=folder/(uuid.uuid4().hex+'.part.mp3')
            try:
                await producer(temp)
                seconds=duration(temp)
                if not _valid_duration(seconds):raise ValueError('invalid synthesized speech duration')
                write_json(pending,{'cache_identity':identity,'sha256':sha256(temp),
                                    'duration_seconds':seconds,'created_at':time.time()})
                os.replace(temp,audio)
                os.replace(pending,receipt)
            finally:
                temp.unlink(missing_ok=True)
        tempdest=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.part')
        try:
            shutil.copyfile(audio,tempdest);os.replace(tempdest,destination)
        finally:tempdest.unlink(missing_ok=True)
    return reused
