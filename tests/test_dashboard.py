import json
import hashlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from workflow.dashboard import make_handler, snapshot, style_catalog, review_packet_catalog
from workflow.queue import Queue


class DashboardTests(unittest.TestCase):
    def test_acceptance_page_rejects_stale_bundle_and_outside_path(self):
        folder=self.root/'review_packets';folder.mkdir()
        page=folder/'acceptance.html';page.write_text('acceptance')
        batch=self.root/'pilot_six_20261001';batch.mkdir()
        bundle=batch/'latest_review_bundle.json';bundle.write_text('{"version":1}')
        data={'acceptance_index_path':str(page),
              'acceptance_bundle_sha256':hashlib.sha256(bundle.read_bytes()).hexdigest()}
        catalog=folder/'catalog.json';catalog.write_text(json.dumps(data))
        self.assertEqual(review_packet_catalog(self.root)['acceptance_index_path'],str(page.resolve()))
        bundle.write_text('{"version":2}')
        self.assertNotIn('acceptance_index_path',review_packet_catalog(self.root))
        data['acceptance_bundle_sha256']=hashlib.sha256(bundle.read_bytes()).hexdigest()
        outside=self.root/'outside.html';outside.write_text('private')
        data['acceptance_index_path']=str(outside);catalog.write_text(json.dumps(data))
        self.assertNotIn('acceptance_index_path',review_packet_catalog(self.root))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.queue = Queue(self.root / 'queue.sqlite')
        self.video = self.root / 'video.mp4'
        self.video.write_bytes(b'0123456789')
        plan = {'title': '测试成片', 'shots': [{'id': 'S1'}]}
        self.queue.submit('test', 'digest', plan, [
            {'id': 'test:S1', 'stage': 'shot', 'capability': 'npu', 'payload': {'data': {'duration_seconds': 4}}, 'deps': []},
            {'id': 'test:export', 'stage': 'export', 'capability': 'cpu', 'payload': {'data': {}}, 'deps': ['test:S1']}])

    def tearDown(self):
        self.tmp.cleanup()

    def finish(self):
        self.queue.finish(self.queue.claim(['npu']), result={'video_path': str(self.video), 'elapsed_seconds': 125, 'metrics': {}})
        self.queue.finish(self.queue.claim(['cpu']), result={'video_path': str(self.video)})

    def test_real_queue_transition_is_visible_without_restart(self):
        initial = snapshot(self.root)['jobs'][0]
        self.assertEqual(initial['status'], 'queued')
        task = self.queue.claim(['npu'])
        running = snapshot(self.root)['jobs'][0]
        self.assertEqual(running['status'], 'running')
        self.assertEqual(running['shot_done'], 0)
        self.queue.finish(task, result={'video_path': str(self.video), 'elapsed_seconds': 125, 'metrics': {}})
        current = snapshot(self.root)
        self.assertEqual(current['jobs'][0]['shot_done'], 1)
        self.assertEqual(current['summary']['generation_seconds_mean'], 125)
        self.assertEqual(current['jobs'][0]['tasks'][0]['video_url'], '/media/video.mp4')
        self.assertNotIn('payload', current['jobs'][0]['tasks'][0])

    def test_range_playback_and_file_allowlist(self):
        self.finish()
        (self.root / 'credentials.json').write_text('{"private":true}')
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.root))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            request = urllib.request.Request(base + '/media/video.mp4', headers={'Range': 'bytes=2-5'})
            with opener.open(request) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), b'2345')
                self.assertEqual(response.headers['Content-Range'], 'bytes 2-5/10')
            for path in ('/media/credentials.json', '/media/%2e%2e/credentials.json'):
                with self.assertRaises(urllib.error.HTTPError) as error: opener.open(base + path)
                self.assertEqual(error.exception.code, 404)
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(urllib.request.Request(base + '/api/status', headers={'Host': 'untrusted.example'}))
            self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_finished_wall_clock_does_not_keep_growing(self):
        self.finish()
        first = snapshot(self.root)['jobs'][0]
        second = snapshot(self.root)['jobs'][0]
        self.assertEqual(first['status'], 'completed')
        self.assertEqual(first['elapsed_seconds'], second['elapsed_seconds'])
        self.assertIsNone(first['eta_seconds_estimate'])

    def test_style_catalog_only_exposes_own_artifacts(self):
        folder=self.root/'style_library';folder.mkdir()
        audio=folder/'audition.mp3';audio.write_bytes(b'audio')
        (folder/'catalog.json').write_text(json.dumps({'items':[{'id':'a','audio_path':str(audio),'video_path':'/etc/passwd'}]}))
        item=style_catalog(self.root)['items'][0]
        self.assertEqual(item['audio_url'],'/media/style_library/audition.mp3')
        self.assertNotIn('video_url',item)
        self.assertNotIn('audio_path',item)

    def test_overlay_derivative_retains_owner_history_and_requires_review(self):
        self.finish()
        derivative = self.root / 'corrected.mp4'; derivative.write_bytes(b'corrected')
        index = self.root / 'postproduction_artifacts.json'
        index.write_text(json.dumps({'test': {'video': str(derivative),
            'programmatic_passed': True, 'audio_packets_unchanged': True}}))
        row = snapshot(self.root)['jobs'][0]
        self.assertEqual(row['id'], 'test')
        self.assertEqual(row['video_url'], '/media/corrected.mp4')
        self.assertEqual(row['original_video_url'], '/media/video.mp4')
        self.assertEqual(row['editorial_status'], 'review_required')
        self.assertFalse(row['postproduction']['content_approved'])
        index.write_text(json.dumps({'test': {'video': '/etc/passwd',
            'programmatic_passed': True, 'audio_packets_unchanged': True}}))
        self.assertEqual(snapshot(self.root)['jobs'][0]['video_url'], '/media/video.mp4')

    def test_review_packets_only_expose_registered_own_assets(self):
        folder=self.root/'review_packets';folder.mkdir()
        page=folder/'index.html';page.write_text('review')
        clip=folder/'clip.mp4';clip.write_bytes(b'clip')
        secret=self.root/'credentials.json';secret.write_text('secret')
        (folder/'catalog.json').write_text(json.dumps({'index_path':str(page),
            'media_paths':[str(clip),str(secret)]}))
        r=review_packet_catalog(self.root)
        self.assertEqual(r['media_urls'],['/media/review_packets/clip.mp4'])
        self.assertEqual(r['index_path'],str(page.resolve()));self.assertFalse(r['content_approved'])
        (folder/'catalog.json').write_text(json.dumps({'index_path':str(secret),'media_paths':[]}))
        self.assertNotIn('index_path',review_packet_catalog(self.root))
