import contextlib
import io
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from workflow.queue import Queue
from workflow.runner import work


class ClaimBusyRecoveryTests(unittest.TestCase):
    def test_real_writer_lock_releases_without_lane_loss_or_task_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            q = Queue(Path(folder) / 'queue.sqlite')
            for jid in ('allowed', 'other'):
                q.submit(jid, jid, {}, [dict(id=jid + ':compose', stage='compose',
                                            capability='cpu', payload={}, deps=[])])
            writer = sqlite3.connect(q.path)
            writer.execute('BEGIN IMMEDIATE')
            observed = []

            class ReleaseWriter(threading.Event):
                def wait(self, timeout=None):
                    # Reached only after the existing bounded BEGIN retry fails.
                    observed.append(q.status())
                    writer.commit()
                    return self.is_set()

            try:
                with patch('workflow.runner.execute', return_value={'elapsed_seconds': 0}) as execute:
                    with contextlib.redirect_stdout(io.StringIO()):
                        work(q, ['cpu'], ReleaseWriter(), job_ids={'allowed'}, stages={'compose'})
                self.assertEqual(len(observed), 1)
                self.assertTrue(all(t['status'] == 'pending' and t['attempts'] == 0
                                    for t in observed[0]))
                self.assertEqual(execute.call_count, 1)
                state = {t['job_id']: t for t in q.status()}
                self.assertEqual((state['allowed']['status'], state['allowed']['attempts']), ('succeeded', 1))
                self.assertEqual((state['other']['status'], state['other']['attempts']), ('pending', 0))
            finally:
                writer.close()

    def test_stop_interrupts_busy_wait_without_execution_or_finish(self):
        for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_BUSY_SNAPSHOT):
            with self.subTest(code=code):
                error = sqlite3.OperationalError('database is locked')
                error.sqlite_errorcode = code
                q = Mock()
                q.claim.side_effect = error

                class StopOnWait(threading.Event):
                    def wait(self, timeout=None):
                        self.set()
                        return True

                with patch('workflow.runner.execute') as execute:
                    work(q, ['cpu'], StopOnWait())
                self.assertEqual(q.claim.call_count, 1)
                execute.assert_not_called()
                q.finish.assert_not_called()

    def test_non_lock_database_errors_still_fail(self):
        for code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_ERROR, None):
            with self.subTest(code=code):
                error = sqlite3.OperationalError('database failure')
                if code is not None:
                    error.sqlite_errorcode = code
                q = Mock()
                q.claim.side_effect = error
                with patch('workflow.runner.execute') as execute:
                    with self.assertRaises(sqlite3.OperationalError):
                        work(q, ['cpu'], threading.Event())
                execute.assert_not_called()
                q.finish.assert_not_called()
