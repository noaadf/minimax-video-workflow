import fcntl
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow.queue import Queue
from workflow.research_inbox import ResearchInbox
from workflow.research_service import Consumer
from tests.test_research_inbox import sample, pack


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.q = Queue(self.root / 'queue.sqlite')
        self.inbox = ResearchInbox(self.q, self.root); self.consumer = Consumer(self.inbox)
        b = sample()
        b['topics'].append({**b['topics'][0], 'id': 'second'})
        self.receipt = self.inbox.receive(pack(b))
        self.config = {'enabled': True, 'max_requests_total': 1, 'production': {}}

    def state(self, rid, value):
        with self.q.connect() as c:
            c.execute('UPDATE research_requests SET state=? WHERE id=?', (value, rid))

    def test_disabled_consumer_does_not_call_models(self):
        with patch.object(self.inbox, 'process', side_effect=AssertionError):
            self.assertEqual(self.consumer.tick({'enabled': False})['state'], 'disabled')

    def test_lifetime_limit_persists_across_consumer_restart(self):
        def process(rid, config):
            self.state(rid, 'verification_failed')
            return {'state': 'verification_failed'}
        with patch.object(self.inbox, 'process', side_effect=process) as call:
            self.consumer.tick(self.config)
            self.assertEqual(Consumer(self.inbox).tick(self.config)['state'], 'idle')
            self.assertEqual(call.call_count, 1)

    def test_no_repeated_work_while_backend_unavailable(self):
        rid = self.receipt['requests'][0]['id']
        def process(rid, config):
            self.state(rid, 'awaiting_backend')
            return {'state': 'awaiting_backend'}
        with patch.object(self.inbox, 'process', side_effect=process) as call:
            self.consumer.tick(self.config)
            for _ in range(3): self.consumer.tick(self.config)
            self.assertEqual(call.call_count, 1)
            self.config['production']['backend'] = {'type': 'assets'}
            self.consumer.tick(self.config)
            self.assertEqual(call.call_count, 2)
            self.consumer.tick(self.config)
            self.assertEqual(call.call_count, 2)
        self.assertEqual(self.inbox.status(self.receipt['bundle_id'])['requests'][0]['state'], 'attention')

    def test_api_failure_isolated_other_request_can_proceed(self):
        self.config['max_requests_total'] = 2
        with patch.object(self.inbox, 'process', side_effect=RuntimeError('fake API failure')) as call:
            first = self.consumer.tick(self.config); second = self.consumer.tick(self.config)
            self.assertNotEqual(first['request_id'], second['request_id'])
            self.consumer.tick(self.config)
            self.assertEqual(call.call_count, 2)
            self.assertEqual(first['state'], 'attention')

    def test_interrupted_request_is_not_replayed(self):
        rid = self.receipt['requests'][0]['id']
        self.state(rid, 'verifying')
        with self.q.connect() as c:
            c.execute('INSERT INTO research_consumption VALUES(?,?,?,NULL,NULL)', (rid, 1, time.time()))
        with patch.object(self.inbox, 'process', side_effect=AssertionError):
            result = self.consumer.tick(self.config)
        self.assertEqual(result['interrupted_requests'], 1)
        self.assertEqual(self.inbox.status(self.receipt['bundle_id'])['requests'][0]['state'], 'attention')

    def test_second_consumer_cannot_process(self):
        with open(self.q.path + '.research-service.lock', 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.consumer.tick(self.config)['state'], 'another_consumer_active')

