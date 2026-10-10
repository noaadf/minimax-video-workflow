"""Single-controller SQLite queue. Keep the DB on local disk, never SFS/NFS."""
import json
from contextlib import contextmanager
import sqlite3
import threading
import time
import uuid
from pathlib import Path


def begin_immediate(c, attempts=4):
    """Retry only lock acquisition, before any transaction/external side effect."""
    original=c.execute('PRAGMA busy_timeout').fetchone()[0]
    c.execute('PRAGMA busy_timeout=2000')
    try:
        for attempt in range(attempts):
            try:
                c.execute('BEGIN IMMEDIATE');return
            except sqlite3.OperationalError as exc:
                code=getattr(exc,'sqlite_errorcode',None)
                if code not in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) or attempt+1>=attempts:raise
                time.sleep(min(.1*2**attempt,.8))
    finally:c.execute(f'PRAGMA busy_timeout={int(original)}')


class LostLease(RuntimeError):
    pass


class Queue:
    def __init__(self, path):
        self.path = str(path)
        self._claim_lock=threading.Lock()  # All local writers share this lock; claims must not starve completion.
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, digest TEXT NOT NULL, manifest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, stage TEXT NOT NULL,
                capability TEXT NOT NULL, payload TEXT NOT NULL, deps TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3, token TEXT, lease_until REAL,
                available_at REAL NOT NULL DEFAULT 0, result TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY, at REAL NOT NULL, task_id TEXT, event TEXT, detail TEXT);
            CREATE INDEX IF NOT EXISTS task_ready ON tasks(status,capability,available_at);
            CREATE INDEX IF NOT EXISTS events_task_event ON events(task_id,event);
            CREATE INDEX IF NOT EXISTS tasks_job ON tasks(job_id);
            ''')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def event(c, tid, event, detail=''):
        c.execute('INSERT INTO events(at,task_id,event,detail) VALUES(?,?,?,?)',
                  (time.time(), tid, event, detail))

    def submit(self, jid, digest, manifest, tasks):
        from .production_control import paused,withdrawn
        if paused(Path(self.path).parent):raise ValueError('production paused by user')
        with self.connect() as c:
            plans={r['id']:json.loads(r['manifest']) for r in c.execute('SELECT id,manifest FROM jobs')}
        plans[jid]=manifest
        if withdrawn(Path(self.path).parent,jid,plans):raise ValueError('product removed by user')
        graph = {t['id']:t.get('deps',[]) for t in tasks}
        if len(graph) != len(tasks):
            raise ValueError('duplicate task identifiers')
        if any(d not in graph for deps in graph.values() for d in deps):
            raise ValueError('task dependency absent from job')
        visiting, visited = set(), set()
        def visit(tid):
            if tid in visiting:
                raise ValueError('cyclic task dependency')
            if tid in visited:
                return
            visiting.add(tid)
            for dep in graph[tid]:
                visit(dep)
            visiting.remove(tid)
            visited.add(tid)
        for tid in graph:
            visit(tid)
        with self.connect() as c:
            begin_immediate(c)
            from .product_approval import locked
            if locked(c,jid,manifest):raise ValueError('product approved: further iteration is locked')
            old = c.execute('SELECT digest FROM jobs WHERE id=?', (jid,)).fetchone()
            if old:
                if old['digest'] != digest:
                    raise ValueError('job id already belongs to a different manifest; use a new id')
                return False
            from .novelty import check as check_novelty
            check_novelty(c,manifest)
            c.execute('INSERT INTO jobs VALUES(?,?,?)', (jid, digest, json.dumps(manifest)))
            for t in tasks:
                c.execute('INSERT INTO tasks(id,job_id,stage,capability,payload,deps) VALUES(?,?,?,?,?,?)',
                          (t['id'], jid, t['stage'], t.get('capability', 'cpu'),
                           json.dumps(t['payload']), json.dumps(t.get('deps', []))))
            self.event(c, jid, 'submitted', digest)
        return True

    def claim(self, capabilities, ttl=120, now=None, job_ids=None, stages=None):
        with self._claim_lock:
            return self._claim(capabilities,ttl,now,job_ids,stages)

    def _block_pending_products(self, c, removed, approved, job_ids, product_id):
        plans={r[0]:json.loads(r[1]) for r in c.execute('SELECT id,manifest FROM jobs')}
        for job in c.execute("SELECT DISTINCT job_id FROM tasks WHERE status='pending'").fetchall():
            key = product_id(job['job_id'], plans)
            if key in removed:
                if job_ids is not None and job['job_id'] not in job_ids:
                    continue
                reason = 'product removed by user: execution blocked'
            elif key in approved:
                reason = 'product approved: iteration locked'
            else:
                continue
            c.execute("UPDATE tasks SET status='blocked',error=? WHERE job_id=? AND status='pending'",
                      (reason, job['job_id']))
            if key in removed:
                self.event(c, job['job_id'], 'withdrawn_blocked', key)

    @staticmethod
    def _dynamic_shot(t, pool, payload):
        return (t['stage'] == 'shot' and t['attempts'] == 0
                   and pool.get('type') == 'pool'
                   and pool.get('scheduling') == 'work_conserving'
                   and not payload.get('data', {}).get('source_path'))

    def _expire_claims(self, c, now, job_ids):
        for t in c.execute("SELECT * FROM tasks WHERE status='running' AND lease_until<?", (now,)).fetchall():
            if job_ids is not None and t['job_id'] not in job_ids:
                continue
            status = ('reconcile' if t['capability'] != 'cpu' or t['stage'] == 'speech' else
                      'failed' if t['attempts'] >= t['max_attempts'] else 'pending')
            c.execute('UPDATE tasks SET status=?,token=NULL,lease_until=NULL,error=? WHERE id=?',
                      (status, 'lease expired; outcome may need remote reconciliation', t['id']))
            self.event(c, t['id'], 'lease_expired')

    def _block_closed_products(self, c, removed, job_ids):
        from .product_approval import initialize
        from .products import product_id
        initialize(c)
        approved={r[0] for r in c.execute('SELECT product_id FROM product_approvals')}
        if approved or removed:
            self._block_pending_products(c, removed, approved, job_ids, product_id)

    def _propagate_claim_failures(self, c, job_ids):
        # Propagate failure transitively, including jobs with no claimable tasks.
        changed = True
        while changed:
            changed = False
            states = {r['id']: r['status'] for r in c.execute('SELECT id,status FROM tasks')}
            for t in c.execute("SELECT id,job_id,deps FROM tasks WHERE status='pending'").fetchall():
                if job_ids is not None and t['job_id'] not in job_ids:
                    continue
                if any(states.get(d) in ('failed', 'blocked', 'reconcile') for d in json.loads(t['deps'])):
                    c.execute("UPDATE tasks SET status='blocked',error='dependency failed' WHERE id=?", (t['id'],))
                    changed = True
        return states

    def _claim_binding(self, c, t, capabilities):
        payload = json.loads(t['payload'])
        chosen = t['capability']
        pool = payload.get('plan', {}).get('backend', {})
        # Bind an unstarted pooled shot only when a device actually claims it.
        # Retries/uncertain remote outcomes remain pinned to their original group.
        dynamic = self._dynamic_shot(t, pool, payload)
        if dynamic:
            from .backends import members, supports_shot
            eligible = [g for g in members(pool)
                        if g['capability'] in capabilities
                        and supports_shot(g, payload['data'])
                        and not c.execute("SELECT 1 FROM tasks WHERE status IN ('running','reconcile') AND capability=?",
                                          (g['capability'],)).fetchone()]
            if not eligible:
                return None
            chosen = eligible[0]['capability']
            payload['backend_override'] = eligible[0]
        elif chosen not in capabilities:
            return None
        return chosen, payload, dynamic

    def _claim_ready(self, c, t, chosen, stages, states):
        if stages is not None and t['stage'] not in stages:
            return False
        if not all(states.get(d) == 'succeeded' for d in json.loads(t['deps'])):
            return False
        # A named accelerator capability is an exclusive device group across all local runners.
        if chosen != 'cpu' and c.execute(
            "SELECT 1 FROM tasks WHERE status IN ('running','reconcile') AND capability=?", (chosen,)).fetchone():
            return False
        # One CPU ASR stage across all runners sharing this queue. Other CPU work proceeds.
        if t['stage'] == 'speech' and c.execute(
            "SELECT 1 FROM tasks WHERE stage='speech' AND status IN ('running','reconcile')").fetchone():
            return False
        return True

    def _claim(self, capabilities, ttl=120, now=None, job_ids=None, stages=None):
        from .production_control import paused, settings
        if paused(Path(self.path).parent):return None
        now = time.time() if now is None else now
        with self.connect() as c:
            begin_immediate(c)
            controls = settings(Path(self.path).parent)
            if controls.get('paused') is True:
                return None
            removed = set(controls.get('withdrawn_products', []))
            self._expire_claims(c, now, job_ids)
            self._block_closed_products(c, removed, job_ids)
            states = self._propagate_claim_failures(c, job_ids)
            # Rotate across films so a newly queued batch progresses together instead
            # of allowing the first film to consume every accelerator group.
            rows = c.execute("""SELECT t.* FROM tasks t
                LEFT JOIN (SELECT prior.job_id,MAX(e.seq) AS last_claim FROM events e
                    JOIN tasks prior ON prior.id=e.task_id WHERE e.event='claimed' GROUP BY prior.job_id) history
                    ON history.job_id=t.job_id
                WHERE t.status='pending' AND t.available_at<=?
                ORDER BY COALESCE(history.last_claim,0), t.rowid""", (now,)).fetchall()
            for t in rows:
                if job_ids is not None and t['job_id'] not in job_ids:
                    continue
                binding = self._claim_binding(c, t, capabilities)
                if binding is None:
                    continue
                chosen, payload, dynamic = binding
                if not self._claim_ready(c, t, chosen, stages, states):
                    continue
                if dynamic:
                    c.execute('UPDATE tasks SET capability=?,payload=? WHERE id=?',
                              (chosen, json.dumps(payload), t['id']))
                    self.event(c, t['id'], 'dispatch_bound', json.dumps({
                        'from': t['capability'], 'to': chosen,
                        'resource_id': payload['backend_override']['resource_id'],
                        'reason': 'idle eligible group claims next unstarted shot'}))
                token = uuid.uuid4().hex
                c.execute("UPDATE tasks SET status='running',attempts=attempts+1,token=?,lease_until=? WHERE id=?",
                          (token, now + ttl, t['id']))
                self.event(c, t['id'], 'claimed', token)
                return {**dict(t), 'token': token, 'attempts': t['attempts'] + 1,
                        'capability': chosen, 'payload': payload, 'deps': json.loads(t['deps'])}
        return None

    def heartbeat(self, tid, token, ttl=120):
        with self._claim_lock:
            return self._heartbeat(tid, token, ttl)

    def _heartbeat(self, tid, token, ttl=120):
        with self.connect() as c:
            n = c.execute("UPDATE tasks SET lease_until=? WHERE id=? AND token=? AND status='running' AND lease_until>?",
                          (time.time() + ttl, tid, token, time.time())).rowcount
            if not n:
                raise LostLease(tid)

    def finish(self, task, result=None, error=None, retryable=False, uncertain=False):
        with self._claim_lock:
            return self._finish(task, result, error, retryable, uncertain)

    def _finish(self, task, result=None, error=None, retryable=False, uncertain=False):
        with self.connect() as c:
            begin_immediate(c)
            t = c.execute("SELECT * FROM tasks WHERE id=? AND token=? AND status='running' AND lease_until>?",
                          (task['id'], task['token'], time.time())).fetchone()
            if not t:
                raise LostLease(task['id'])
            status = 'reconcile' if uncertain else 'succeeded' if error is None else (
                'pending' if retryable and t['attempts'] < t['max_attempts'] else 'failed')
            c.execute('UPDATE tasks SET status=?,result=?,error=?,available_at=?,token=NULL,lease_until=NULL WHERE id=?',
                      (status, json.dumps(result) if result else None, error,
                       time.time() + min(60, 2 ** t['attempts']) if status == 'pending' else 0, task['id']))
            self.event(c, task['id'], status, error or '')

    def results(self, deps):
        with self.connect() as c:
            return {d: json.loads(c.execute('SELECT result FROM tasks WHERE id=?', (d,)).fetchone()['result']) for d in deps}

    def status(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT id,job_id,stage,capability,status,attempts,error,result FROM tasks ORDER BY rowid')]
