import copy
import hashlib
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from workflow.queue import Queue
from workflow.research_inbox import ResearchInbox, parse_package
from workflow.research_gateway import handler


def sample():
    return {'schema_version': 1, 'bundle_id': 'synthetic_test_only',
            'sources': [{'id': 'r1', 'url': 'https://example.com/reference', 'creator': 'Synthetic fixture',
                         'title': 'Test only', 'duration_seconds': 10, 'visual_status': 'observed', 'audio_status': 'unreviewed'}],
            'shots': [{'id': 's1', 'source_id': 'r1', 'start_seconds': 0, 'end_seconds': 3,
                       'observation': 'Synthetic test observation', 'keyframes': ['frame.txt']}],
            'templates': [{'id': 't1', 'compatible_templates': ['science_reveal'], 'structure': ['question', 'evidence'],
                           'shot_ids': ['s1'], 'original_script': 'Synthetic original example', 'comment_angles': ['What tradeoff matters?']}],
            'topics': [{'id': 'p1', 'title': 'Test proposal', 'theme': 'science',
                        'primary_source_urls': ['https://www.cas.cn/test'], 'template_id': 't1'}],
            'assets': {'frame.txt': hashlib.sha256(b'fixture').hexdigest()}}


def pack(b, extra=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('bundle.json', json.dumps(b))
        z.writestr('frame.txt', b'fixture')
        if extra: z.writestr(extra, b'bad')
    return out.getvalue()


class ResearchInboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.q = Queue(self.root / 'queue.sqlite')
        self.inbox = ResearchInbox(self.q, self.root)
    def tearDown(self): self.tmp.cleanup()

    def test_import_is_not_approval_or_generation(self):
        r = self.inbox.receive(pack(sample()))
        self.assertFalse(r['content_approved'])
        self.assertEqual(r['audio_unreviewed'], 1)
        self.assertEqual(r['requests'][0]['state'], 'awaiting_source_verification')
        self.assertEqual(self.q.status(), [])
        cfg = self.inbox.verification_config(r['requests'][0]['id'])
        self.assertEqual(cfg['seeds'], ['https://www.cas.cn/test'])
        self.assertFalse(cfg['discover_links'])

    def test_replay_and_renamed_content_deduplicate(self):
        b = sample(); first = self.inbox.receive(pack(b))
        self.assertTrue(self.inbox.receive(pack(b))['duplicate'])
        b['bundle_id'] = 'renamed'
        self.assertEqual(self.inbox.receive(pack(b))['bundle_id'], first['bundle_id'])
        with self.q.connect() as c:
            self.assertEqual(c.execute('select count(*) from research_requests').fetchone()[0], 1)

    def test_existing_id_cannot_be_overwritten(self):
        b = sample(); self.inbox.receive(pack(b)); b['shots'][0]['observation'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'immutable'): self.inbox.receive(pack(b))

    def test_hash_traversal_inventory_and_approval_fields_rejected(self):
        b = sample(); b['assets']['frame.txt'] = '0' * 64
        with self.assertRaises(ValueError): parse_package(pack(b))
        with self.assertRaises(ValueError): parse_package(pack(sample(), '../outside'))
        b = sample(); b['content_approved'] = True
        with self.assertRaises(ValueError): parse_package(pack(b))
        self.assertEqual(list(self.inbox.root.iterdir()), [])

    def test_time_and_evidence_validation(self):
        mutations = [lambda b: b['shots'][0].update(end_seconds=11),
                     lambda b: b['shots'][0].update(start_seconds=float('nan')),
                     lambda b: b['sources'][0].update(visual_status='unavailable'),
                     lambda b: b['shots'][0].update(keyframes=['missing.png']),
                     lambda b: b['topics'][0].update(primary_source_urls=['https://127.0.0.1/a'])]
        for mutate in mutations:
            b = sample(); mutate(b)
            with self.assertRaises(ValueError): parse_package(pack(b))

    def test_concurrent_replays_single_request(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda _: self.inbox.receive(pack(sample())), range(4)))
        self.assertEqual(sum(not x['duplicate'] for x in results), 1)

    def test_verification_failure_never_calls_producer(self):
        rid = self.inbox.receive(pack(sample()))['requests'][0]['id']
        def forbidden(*args): raise AssertionError('must not dispatch unsupported evidence')
        result = self.inbox.process(rid, {}, collector=lambda *args: {'topics': []}, producer=forbidden)
        self.assertEqual(result['state'], 'verification_failed')
        self.assertEqual(self.inbox.process(rid, {}, collector=forbidden)['state'], 'verification_failed')
        self.assertEqual(self.q.status(), [])

    def test_worker_uses_verified_catalog_not_dot_claims_and_receipt_tracks_state(self):
        receipt = self.inbox.receive(pack(sample())); rid = receipt['requests'][0]['id']
        verified = {'topics': [{'id': 'verified_test_only'}]}
        calls = []
        def consumer(q, catalog, cfg, root):
            self.assertEqual(catalog, verified)
            self.assertEqual(cfg['limit'], 1)
            self.assertTrue(Path(cfg['style_library_file']).is_file())
            calls.append(catalog)
            return [{'state': 'awaiting_backend'}]
        result = self.inbox.process(rid, {'use_imported_style': True, 'limit': 20}, collector=lambda *args: verified, producer=consumer)
        self.assertEqual(result['state'], 'awaiting_backend')
        self.assertEqual(self.inbox.status(receipt['bundle_id'])['requests'][0]['state'], 'awaiting_backend')
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.q.status(), [])

    def test_verified_proposal_reaches_real_task_queue_once(self):
        from workflow.batch import produce
        plan = json.loads(Path('workflow/examples/smoke_bridge.json').read_text())
        topic = {'id': 'verified_fixture', 'status': 'source_supported', 'title': 'Synthetic integration test',
                 'theme': 'engineering', 'facts': plan['facts'], 'source_url': plan['facts'][0]['source_url'],
                 'published': '2026-10-02'}
        def planner(brief, path):
            result = {**plan, 'id': brief['id']}
            Path(path).write_text(json.dumps(result))
            return result
        def producer(q, catalog, config, root):
            return produce(q, catalog, config, root, planner=planner)
        receipt = self.inbox.receive(pack(sample())); rid = receipt['requests'][0]['id']
        cfg = {'music': plan['music'], 'backend': {'type': 'assets'}}
        result = self.inbox.process(rid, cfg, collector=lambda *args: {'topics': [topic]}, producer=producer)
        self.assertEqual(result['state'], 'enqueued')
        count = len(self.q.status())
        self.assertGreater(count, 0)
        from workflow.controller import Controller
        controller = Controller(self.q, self.root)
        self.assertEqual(controller.active_jobs(), [result['results'][0]['job_id']])
        self.assertTrue(self.inbox.process(rid, cfg)['duplicate'])
        self.assertEqual(len(self.q.status()), count)
        self.assertEqual(len(controller.items()), 1)
        status = self.inbox.status(receipt['bundle_id'])
        self.assertEqual(status['requests'][0]['jobs'][0]['task_counts'], {'pending': count})
        self.assertFalse(status['content_approved'])

    def test_backend_resume_reuses_verified_catalog_and_stale_catalog_holds(self):
        import datetime as dt
        from workflow.plan import write_json
        rid = self.inbox.receive(pack(sample()))['requests'][0]['id']
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
        catalog = {'topics': [{'id': 'fixture', 'published': today}]}
        calls = []
        def collect(config, folder):
            calls.append('collect'); write_json(Path(folder) / 'catalog.json', catalog); return catalog
        def produce(*args): calls.append('produce'); return [{'state': 'awaiting_backend'}]
        self.inbox.process(rid, {}, collector=collect, producer=produce)
        self.inbox.process(rid, {}, collector=collect, producer=produce)
        self.assertEqual(calls, ['collect', 'produce', 'produce'])
        # A delayed queue admission must not describe an old source as a fresh hotspot.
        with patch('workflow.ingest.in_window', return_value=False):
            result = self.inbox.process(rid, {}, collector=collect, producer=produce)
        self.assertEqual(result['state'], 'verification_failed')
        self.assertTrue(result['stale_source_catalog'])
        self.assertEqual(calls, ['collect', 'produce', 'produce'])

    def test_http_authenticated_submission_status_and_no_dispatch(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.inbox, 'x' * 32))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = 'http://127.0.0.1:' + str(server.server_port) + '/v1/research-bundles'
        try:
            with self.assertRaises(urllib.error.HTTPError) as err: urllib.request.urlopen(url)
            self.assertEqual(err.exception.code, 401)
            req = urllib.request.Request(url, data=pack(sample()), headers={'Authorization': 'Bearer ' + 'x' * 32, 'Content-Type': 'application/zip'})
            with urllib.request.urlopen(req) as response:
                self.assertEqual(response.status, 201); receipt = json.load(response)
            req = urllib.request.Request(url + '/' + receipt['bundle_id'], headers={'Authorization': 'Bearer ' + 'x' * 32})
            with urllib.request.urlopen(req) as response: self.assertEqual(json.load(response)['shots'], 1)
            self.assertEqual(self.q.status(), [])
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
