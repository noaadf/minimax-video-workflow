import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from workflow.reference_dashboard import handler,snapshot

class ReferenceViewerTests(unittest.TestCase):
    def test_video_range_and_source_allowlist(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder=root/'BVtest';folder.mkdir();(folder/'source.mp4').write_bytes(b'0123456789')
            (folder/'secret.json').write_text('private')
            server=ThreadingHTTPServer(('127.0.0.1',0),handler(root));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}';opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(urllib.request.Request(base+'/asset/BVtest/source.mp4',headers={'Range':'bytes=2-5'})) as r:
                    self.assertEqual(r.status,206);self.assertEqual(r.read(),b'2345')
                for path in ['/asset/BVtest/secret.json','/asset/BVtest/../secret.json']:
                    with self.assertRaises(urllib.error.HTTPError):opener.open(base+path)
            finally:server.shutdown();server.server_close();thread.join()

    def test_partial_review_does_not_claim_full_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder=root/'BVtest';(folder/'full/reviews').mkdir(parents=True)
            (folder/'evidence.json').write_text(json.dumps({'creator':'test','title':'film','source_url':'https://source','duration_seconds':100}))
            (folder/'full/reviews/000.json').write_text(json.dumps({'batch_id':0,'analysis':{'shots':[{'start':0,'end':10}]}}))
            v=snapshot(root)['videos'][0];self.assertEqual(v['reviewed_seconds'],10);self.assertIsNone(v['summary'])

class FullTimelineTests(unittest.TestCase):
    def test_validation_rejects_missing_middle_or_end(self):
        spec=importlib.util.spec_from_file_location('reference_review','scripts/full_reference_review.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        fields={k:'observed' for k in ('visual','scale','motion','cut_in','narration_summary','rhetorical_role','av_relation','engagement_device','reusable_move','boundary_precision','confidence')}
        with self.assertRaises(ValueError):module.validate({'shots':[{**fields,'start':0,'end':8}]},{'start':0,'end':10})
        with self.assertRaises(ValueError):module.validate({'shots':[{**fields,'start':0,'end':4},{**fields,'start':5,'end':10}]},{'start':0,'end':10})
        self.assertEqual(len(module.validate({'shots':[{**fields,'start':0,'end':10}]},{'start':0,'end':10})['shots']),1)
