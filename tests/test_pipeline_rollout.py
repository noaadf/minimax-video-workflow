import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from workflow.pipeline_status import snapshot
from workflow.dashboard import make_handler
from workflow.queue import Queue


class PipelineRolloutTests(unittest.TestCase):
    def test_selected_film_does_not_show_other_products_global_frames(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);Queue(root/'queue.sqlite')
            folder=root/'storyboard_rollout';frames=folder/'keyframes';frames.mkdir(parents=True)
            (frames/'OTHER_start.png').write_bytes(b'other product')
            (folder/'status.json').write_text(json.dumps({'job_id':'missing-selected-film','state':'film_needs_revision','film_ready':False}))
            result=snapshot(root)
            self.assertEqual(result['keyframes'],[])
            self.assertFalse(result['film_ready'])

    def test_absent_status_is_not_reported_as_deployed(self):
        with tempfile.TemporaryDirectory() as d:
            result=snapshot(d)
            self.assertFalse(result['deployed'])
            self.assertFalse(result['film_ready'])
            self.assertEqual(result['state'],'not_deployed')
            self.assertEqual(result['keyframes'],[])

    def test_endpoint_assets_preview_and_escape_protection(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);Queue(root/'queue.sqlite')
            folder=root/'storyboard_rollout';frames=folder/'keyframes';frames.mkdir(parents=True)
            (frames/'A01_start.png').write_bytes(b'picture')
            private=root/'private.png';private.write_bytes(b'private')
            (frames/'escaped.png').symlink_to(private)
            preview=folder/'preview.mp4';preview.write_bytes(b'preview')
            status=folder/'status.json';status.write_text(json.dumps({'state':'preflight_running',
                'deployed':True,'preview_path':str(preview),'film_ready':False}))
            before=status.read_bytes()
            server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(root))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                result=json.loads(opener.open(base+'/api/pipeline').read())
                self.assertTrue(result['deployed']);self.assertFalse(result['film_ready'])
                self.assertEqual(result['keyframes_count'],1)
                self.assertEqual(opener.open(base+result['keyframes'][0]['url']).read(),b'picture')
                self.assertEqual(opener.open(base+result['preview_url']).read(),b'preview')
                self.assertIn('静帧',result['preview_kind'])
                self.assertIn('首尾关键帧',opener.open(base+'/pipeline').read().decode())
                self.assertEqual(status.read_bytes(),before)
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_resident_cpu_lane_can_claim_story_preflight(self):
        from scripts.run_deadline30 import CPU_MEDIA_STAGES
        with tempfile.TemporaryDirectory() as d:
            q=Queue(Path(d)/'q.sqlite')
            q.submit('story','hash',{},[{'id':'story:story_preflight','stage':'story_preflight',
                'capability':'cpu','payload':{},'deps':[]}])
            self.assertEqual(q.claim(['cpu'],stages=CPU_MEDIA_STAGES)['stage'],'story_preflight')
