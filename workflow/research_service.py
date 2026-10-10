"""Bounded persistent CPU consumer for uploaded research; no accelerator runner."""
import argparse
import fcntl
import json
import time
from pathlib import Path

from .backends import ready
from .core.io import write_json
from .queue import Queue
from .research_inbox import ResearchInbox


class Consumer:
    def __init__(self, inbox):
        self.inbox = inbox
        self.queue = inbox.queue
        with self.queue.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS research_consumption(
                request_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL,
                started REAL NOT NULL, finished REAL, outcome TEXT)''')

    def tick(self, config):
        """At most one request; unavailable backends and terminal errors do not busy-retry."""
        if config.get('enabled') is not True:
            return {'state': 'disabled'}
        limit = config.get('max_requests_total')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('explicit max_requests_total must be 1..100')
        production = config['production']
        with open(self.queue.path + '.research-service.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'state': 'another_consumer_active'}
            backend = production.get('backend', {})
            if production.get('backend_file'):
                backend = json.loads(Path(production['backend_file']).expanduser().read_text(encoding="utf-8"))
            with self.queue.connect() as c:
                # A prior consumer died during an API/queue operation. Quarantine for
                # outcome reconciliation, never blindly repeat a potentially billed call.
                abandoned = c.execute('SELECT request_id FROM research_consumption WHERE finished IS NULL').fetchall()
                for r in abandoned:
                    c.execute('UPDATE research_requests SET state=? WHERE id=?', ('attention', r['request_id']))
                    c.execute('UPDATE research_consumption SET finished=?,outcome=? WHERE request_id=?',
                              (time.time(), 'interrupted_requires_reconciliation', r['request_id']))
                used = c.execute('SELECT count(*) FROM research_consumption').fetchone()[0]
                rows = c.execute("SELECT id,state FROM research_requests WHERE state IN ('awaiting_source_verification','awaiting_backend','awaiting_storyboard') ORDER BY rowid").fetchall()
                selected = None
                for row in rows:
                    prior = c.execute('SELECT * FROM research_consumption WHERE request_id=?', (row['id'],)).fetchone()
                    if not prior and used >= limit: continue
                    if row['state'] == 'awaiting_backend' and not ready(backend): continue
                    if row['state'] == 'awaiting_storyboard':
                        from .storyboard import missing_previsuals
                        prior_result = self.inbox.root / 'processing' / row['id'] / 'result.json'
                        try:
                            results=json.loads(prior_result.read_text(encoding="utf-8"))['results']
                            paths=[Path(r['manifest_path']) for r in results]
                            if not paths or not ready(backend) or any(missing_previsuals(json.loads(p.read_text(encoding="utf-8")),p.parent) for p in paths):
                                continue
                        except (OSError,ValueError,KeyError,TypeError):
                            continue
                    # One initial planning attempt and one backend-resume attempt.
                    if prior and prior['attempts'] >= 2:
                        c.execute('UPDATE research_requests SET state=? WHERE id=?', ('attention', row['id']))
                        continue
                    selected = dict(row)
                    c.execute('INSERT INTO research_consumption VALUES(?,?,?,NULL,NULL) ON CONFLICT(request_id) DO UPDATE SET attempts=attempts+1,started=excluded.started,finished=NULL,outcome=NULL',
                              (row['id'], 1, time.time()))
                    break
            if selected is None:
                return {'state': 'idle', 'requests_started': used, 'limit': limit,
                        'interrupted_requests': len(abandoned)}
            try:
                result = self.inbox.process(selected['id'], production)
            except Exception as exc:
                result = {'request_id': selected['id'], 'state': 'attention', 'error_type': type(exc).__name__}
                with self.queue.connect() as c:
                    c.execute('UPDATE research_requests SET state=? WHERE id=?', ('attention', selected['id']))
            with self.queue.connect() as c:
                c.execute('UPDATE research_consumption SET finished=?,outcome=? WHERE request_id=?',
                          (time.time(), json.dumps(result, ensure_ascii=False), selected['id']))
            return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', default='output/production')
    p.add_argument('--config', required=True)
    p.add_argument('--once', action='store_true')
    p.add_argument('--interval', type=int, default=30)
    args = p.parse_args()
    if not 5 <= args.interval <= 300: p.error('interval must be 5..300 seconds')
    root = Path(args.root).resolve()
    consumer = Consumer(ResearchInbox(Queue(root / 'queue.sqlite'), root))
    while True:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        result = consumer.tick(config)
        write_json(root / 'research_consumer_status.json', {'at': time.time(), **result})
        if result['state'] not in ('idle', 'disabled', 'another_consumer_active'):
            print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.once: break
        time.sleep(args.interval)


if __name__ == '__main__': main()
