import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import cloud_speech, timed_speech_worker
from workflow.plan import sha256


class TimedCloudTests(unittest.TestCase):
    def test_worker_captures_boundaries_once_and_rejects_stale_pair(self):
        calls=[]
        class Edge:
            def __init__(self,text,*args,**kwargs):
                calls.append(kwargs);self.text=text
            async def stream(self):
                yield {'type':'audio','data':b'accepted_audio'*100}
                yield {'type':'WordBoundary','offset':0,'duration':10000000,'text':'你好'}
        request={'text':'你好。','voice':{},'boundary':'WordBoundary'}
        with tempfile.TemporaryDirectory() as d,patch.dict('sys.modules',{'edge_tts':types.SimpleNamespace(Communicate=Edge)}):
            a=timed_speech_worker.serve(request,d);b=timed_speech_worker.serve(request,d)
            self.assertEqual(a,b);self.assertEqual(len(calls),1)
            self.assertEqual(calls[0]['boundary'],'WordBoundary')
            self.assertNotEqual(cloud_speech.identity(request),cloud_speech.identity({'text':'你好。','voice':{}}))
            Path(a['words_path']).write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'artifact changed'):timed_speech_worker.serve(request,d)
            self.assertEqual(len(calls),1)

    def test_adapter_binds_both_remote_artifacts_to_local_audio(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);audio=root/'voice.mp3';words=root/'words.json'
            rawaudio=b'accepted_audio';fixture=root/'fixture.mp3';fixture.write_bytes(rawaudio)
            raw={'script_text':'你好。','audio_sha256':sha256(fixture),
                 'boundaries':[{'offset':0,'duration':10000000,'text':'你好'}]}
            rawbytes=json.dumps(raw).encode();sidecar=root/'fixture.json';sidecar.write_bytes(rawbytes)
            req={'text':'你好。','voice':{},'boundary':'WordBoundary'};rid=cloud_speech.identity(req)
            cfg={'host':'example','port':22,'identity_file':'/key','code_root':'/runtime','state_root':'/state','python':'python'}
            remote={'request_id':rid,'path':f'/state/{rid}/speech.mp3','sha256':sha256(fixture),
                    'words_path':f'/state/{rid}/words.json','words_sha256':sha256(sidecar)}
            commands=[]
            def run(command,**kwargs):
                commands.append(command)
                if command[0]=='scp' and command[-2].endswith('/speech.mp3'):Path(command[-1]).write_bytes(rawaudio)
                if command[0]=='scp' and command[-2].endswith('/words.json'):Path(command[-1]).write_bytes(rawbytes)
                return types.SimpleNamespace(returncode=0,stdout=json.dumps(remote).encode() if 'input' in kwargs else b'')
            with patch('workflow.cloud_speech.subprocess.run',side_effect=run),patch('workflow.media.duration',return_value=1.2):
                cloud_speech.synthesize_timed('你好。',{},audio,words,cfg)
            cue=json.loads(words.read_text())
            self.assertEqual(cue['source_path'],str(audio.resolve()))
            self.assertEqual(cue['source_sha256'],sha256(audio))
            self.assertEqual(cue['words'][0]['end'],1)
            self.assertTrue(any('timed_speech_v1' in str(c) for c in commands))

    def test_failed_worker_stream_is_never_automatically_replayed(self):
        calls=[]
        class Edge:
            def __init__(self,*args,**kwargs):calls.append(1)
            async def stream(self):
                yield {'type':'audio','data':b'audio'*200}
                raise RuntimeError('stream failed')
        req={'text':'你好。','voice':{},'boundary':'WordBoundary'}
        with tempfile.TemporaryDirectory() as d,patch.dict('sys.modules',{'edge_tts':types.SimpleNamespace(Communicate=Edge)}):
            with self.assertRaisesRegex(RuntimeError,'stream failed'):timed_speech_worker.serve(req,d)
            with self.assertRaisesRegex(RuntimeError,'incomplete'):timed_speech_worker.serve(req,d)
            self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
