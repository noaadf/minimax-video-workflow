import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow.queue import Queue


class QueueWithdrawalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.queue = Queue(self.root / 'queue.sqlite')

    def submit(self, jid, plan=None, capability='cpu'):
        self.queue.submit(jid, jid, plan or {'id': jid}, [
            {'id': jid + ':shot', 'stage': 'shot', 'capability': capability,
             'payload': {}, 'deps': []},
            {'id': jid + ':compose', 'stage': 'compose', 'payload': {},
             'deps': [jid + ':shot']},
        ])

    def withdraw(self, *products):
        (self.root / 'production_control.json').write_text(
            json.dumps({'withdrawn_products': list(products)}))

    def states(self):
        return {row['id']: row['status'] for row in self.queue.status()}

    def test_queued_root_and_repair_family_are_blocked(self):
        self.submit('original')
        self.submit('repair', {'parent_job_id': 'original'})
        self.submit('grandchild', {'repair_parent_id': 'repair'})
        self.submit('explicit', {'product_id': 'original'})
        self.submit('rooted', {'root_job_id': 'original'})
        self.submit('other')
        self.withdraw('original')

        # A separate connection must honor a withdrawal made after admission.
        second = Queue(self.queue.path)
        task = second.claim(['cpu'])
        self.assertEqual(task['id'], 'other:shot')
        for jid in ('original', 'repair', 'grandchild', 'explicit', 'rooted'):
            for stage in ('shot', 'compose'):
                self.assertEqual(self.states()[jid + ':' + stage], 'blocked')
        with self.queue.connect() as c:
            self.assertEqual(c.execute("SELECT SUM(attempts) FROM tasks WHERE status='blocked'").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 6)
        second.finish(task, result={'ok': True})
        self.assertEqual(second.claim(['cpu'])['id'], 'other:compose')
        self.assertIsNone(second.claim(['cpu']))
        with self.queue.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM events WHERE event='withdrawn_blocked'").fetchone()[0], 5)

    def test_pause_changed_while_acquiring_transaction_prevents_claim(self):
        self.submit('original')
        from workflow.queue import begin_immediate

        def acquire(c):
            begin_immediate(c)
            (self.root / 'production_control.json').write_text(
                json.dumps({'paused': True}))

        with patch('workflow.queue.begin_immediate', side_effect=acquire):
            self.assertIsNone(self.queue.claim(['cpu']))
        self.assertEqual(set(self.states().values()), {'pending'})
        self.assertEqual(self.queue.status()[0]['attempts'], 0)

    def test_withdrawal_does_not_cancel_inflight_or_erase_receipts(self):
        self.submit('original')
        task = self.queue.claim(['cpu'])
        self.withdraw('original')
        self.assertIsNone(self.queue.claim(['cpu']))
        self.assertEqual(self.states()['original:shot'], 'running')
        self.queue.finish(task, result={'receipt': 'retained'})
        self.assertEqual(self.states()['original:shot'], 'succeeded')
        self.assertEqual(self.states()['original:compose'], 'blocked')
        self.assertEqual(self.queue.results(['original:shot']),
                         {'original:shot': {'receipt': 'retained'}})

    def test_expired_accelerator_still_requires_reconciliation(self):
        self.submit('original', capability='npu')
        self.queue.claim(['npu'], ttl=-1)
        self.withdraw('original')
        self.assertIsNone(self.queue.claim(['npu']))
        self.assertEqual(self.states()['original:shot'], 'reconcile')
        self.assertEqual(self.states()['original:compose'], 'blocked')

    def test_retry_cannot_resume_withdrawn_work(self):
        self.submit('original')
        task = self.queue.claim(['cpu'])
        self.queue.finish(task, error='transient', retryable=True)
        self.withdraw('original')
        self.assertIsNone(self.queue.claim(['cpu'], now=10**12))
        self.assertEqual(set(self.states().values()), {'blocked'})
        self.assertEqual(self.queue.status()[0]['attempts'], 1)

    def test_job_filter_leaves_unrelated_queue_rows_untouched(self):
        self.submit('original')
        self.submit('other')
        self.withdraw('original')
        self.assertEqual(self.queue.claim(['cpu'], job_ids={'other'})['id'], 'other:shot')
        self.assertEqual(self.states()['original:shot'], 'pending')
        self.assertIsNone(self.queue.claim(['cpu'], job_ids={'original'}))
        self.assertEqual(self.states()['original:shot'], 'blocked')

    def test_legacy_dated_pilot_identity_is_protected(self):
        root = 'pilot6_bridge_a_20261001'
        self.submit(root + '-repair1')
        self.withdraw(root)
        self.assertIsNone(self.queue.claim(['cpu']))
        self.assertEqual(set(self.states().values()), {'blocked'})

    def test_clearing_withdrawal_does_not_automatically_restore_blocked_work(self):
        self.submit('original')
        self.withdraw('original')
        self.assertIsNone(self.queue.claim(['cpu']))
        self.withdraw()
        self.assertIsNone(self.queue.claim(['cpu']))
        self.assertEqual(set(self.states().values()), {'blocked'})
