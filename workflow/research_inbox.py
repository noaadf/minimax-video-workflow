"""Untrusted research ZIP -> immutable evidence store and editorial inbox.

No model calls, URL fetching, shell execution, production submission or approval.
The production controller remains the only owner of accelerator work.
"""
import hashlib
import io
import json
import math
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .ingest import allowed_url, DEFAULT_DOMAINS
from .core.io import digest
from .plan import TEMPLATES

MAX_BYTES = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value), 'invalid id')
    return value


def text(value):
    require(isinstance(value, str) and 0 < len(value.strip()) <= 20000, 'nonempty bounded text required')


def number(value):
    require(type(value) in (int, float) and math.isfinite(value), 'finite number required')
    return value


def asset_path(value):
    require(isinstance(value, str) and '\\' not in value, 'invalid asset path')
    p = PurePosixPath(value)
    require(not p.is_absolute() and p.parts and all(x not in ('..', '.') for x in p.parts)
            and str(p) == value and value != 'bundle.json', 'unsafe asset path')
    return value


def _require_zip_inventory(z):
    infos = z.infolist()
    require(0 < len(infos) <= 2000, 'too many ZIP entries')
    names = [i.filename for i in infos]
    require(len(names) == len(set(names)) and 'bundle.json' in names, 'duplicate entries or missing bundle.json')
    require(sum(i.file_size for i in infos) <= MAX_EXPANDED, 'expanded package exceeds 256 MiB')
    for i in infos:
        require(not i.is_dir() and (i.external_attr >> 16) & 0o170000 != 0o120000, 'directories/symlinks not accepted')
        if i.filename != 'bundle.json':
            asset_path(i.filename)
    require(z.getinfo('bundle.json').file_size <= 4 * 1024 * 1024, 'manifest too large')
    return names


def _validate_template_text(t):
    for k in ('structure', 'comment_angles'):
        require(isinstance(t[k], list) and t[k], 'missing ' + k)
        for v in t[k]: text(v)
    text(t['original_script'])


def _read_package(blob):
    require(len(blob) <= MAX_BYTES, 'package exceeds 64 MiB')
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = _require_zip_inventory(z)
        def no_duplicates(pairs):
            result = {}
            for k, v in pairs:
                require(k not in result, 'duplicate JSON field')
                result[k] = v
            return result
        b = json.loads(z.read('bundle.json'), object_pairs_hook=no_duplicates)
        require(isinstance(b, dict) and set(b) == {'schema_version', 'bundle_id', 'sources', 'shots', 'templates', 'topics', 'assets'}, 'invalid bundle fields')
        require(type(b['schema_version']) is int and b['schema_version'] == 1, 'schema_version must be 1')
        identifier(b['bundle_id'])
        require(isinstance(b['assets'], dict), 'asset hash map required')
        require(set(names) == {'bundle.json', *b['assets']}, 'ZIP inventory differs from asset hash map')
        assets = {}
        for path, sha in b['assets'].items():
            asset_path(path)
            require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha), 'invalid asset SHA256')
            data = z.read(path)
            require(hashlib.sha256(data).hexdigest() == sha, 'asset checksum mismatch: ' + path)
            assets[path] = data
    return b, assets


def _validate_sources(b):
    sources = {}
    for s in b['sources']:
        require(set(s) == {'id', 'url', 'creator', 'title', 'duration_seconds', 'visual_status', 'audio_status'}, 'invalid source fields')
        sid = identifier(s['id']); require(sid not in sources, 'duplicate source id')
        u = urlsplit(s['url'])
        require(u.scheme == 'https' and u.hostname and not u.username and not u.password and u.port in (None, 443), 'source requires HTTPS')
        text(s['creator']); text(s['title'])
        require(0 < number(s['duration_seconds']) <= 86400, 'invalid source duration')
        require(s['visual_status'] in ('observed', 'unavailable'), 'invalid visual status')
        require(s['audio_status'] in ('unreviewed', 'transcribed', 'listened'), 'invalid audio status')
        sources[sid] = s
    return sources


def _validate_shots(b, sources, assets):
    shots = {}
    last = {}
    for s in b['shots']:
        require(set(s) == {'id', 'source_id', 'start_seconds', 'end_seconds', 'observation', 'keyframes'}, 'invalid shot fields')
        sid = identifier(s['id']); require(sid not in shots, 'duplicate shot id')
        require(s['source_id'] in sources, 'unknown shot source')
        src = sources[s['source_id']]
        require(src['visual_status'] == 'observed', 'unavailable source cannot supply observations')
        require(last.get(src['id'], 0) <= number(s['start_seconds']) < number(s['end_seconds']) <= src['duration_seconds'], 'overlapping/unordered/out-of-range timecode')
        last[src['id']] = s['end_seconds']; text(s['observation'])
        require(isinstance(s['keyframes'], list) and s['keyframes'] and all(x in assets for x in s['keyframes']), 'shot needs packaged keyframe evidence')
        shots[sid] = s
    return shots


def _validate_templates(b, shots):
    ids = set()
    for t in b['templates']:
        require(set(t) == {'id', 'compatible_templates', 'structure', 'shot_ids', 'original_script', 'comment_angles'}, 'invalid template fields')
        tid = identifier(t['id']); require(tid not in ids, 'duplicate template id'); ids.add(tid)
        require(isinstance(t['compatible_templates'], list) and t['compatible_templates'] and all(x in TEMPLATES for x in t['compatible_templates']), 'unknown production template')
        require(isinstance(t['shot_ids'], list) and t['shot_ids'] and all(x in shots for x in t['shot_ids']), 'template needs valid shot evidence')
        _validate_template_text(t)

def _validate_topics(b):
    ids = set()
    for t in b['topics']:
        require(set(t) == {'id', 'title', 'theme', 'primary_source_urls', 'template_id'}, 'invalid topic fields')
        tid = identifier(t['id']); require(tid not in ids, 'duplicate topic id'); ids.add(tid)
        text(t['title'])
        require(t['theme'] in ('engineering', 'science', 'product', 'global'), 'invalid theme')
        require(t['template_id'] in {x['id'] for x in b['templates']}, 'unknown topic template')
        require(isinstance(t['primary_source_urls'], list) and 1 <= len(t['primary_source_urls']) <= 10, 'primary sources required')
        t['primary_source_urls'] = sorted(set(allowed_url(x, DEFAULT_DOMAINS) for x in t['primary_source_urls']))


def parse_package(blob):
    b, assets = _read_package(blob)
    for key, limit in [('sources', 100), ('shots', 2000), ('templates', 100), ('topics', 100)]:
        require(isinstance(b[key], list) and len(b[key]) <= limit, 'invalid ' + key)
    require(b['sources'], 'at least one source required')
    sources = _validate_sources(b)
    shots = _validate_shots(b, sources, assets)
    _validate_templates(b, shots)
    _validate_topics(b)
    return b, assets


class ResearchInbox:
    def __init__(self, queue, root):
        self.queue = queue
        self.root = Path(root).resolve() / 'research_inbox'
        self.root.mkdir(parents=True, exist_ok=True)
        with queue.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS research_bundles(
                  id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, path TEXT NOT NULL,
                  receipt TEXT NOT NULL, received REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS research_requests(
                  id TEXT PRIMARY KEY, bundle_id TEXT NOT NULL, topic_id TEXT NOT NULL,
                  state TEXT NOT NULL, payload TEXT NOT NULL);
            ''')

    def receive(self, blob):
        b, assets = parse_package(blob)
        content = {k: v for k, v in b.items() if k != 'bundle_id'}
        key = digest(content)
        with self.queue.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT * FROM research_bundles WHERE id=?', (b['bundle_id'],)).fetchone()
            if old:
                require(old['digest'] == key, 'bundle id is immutable; use a new revision id')
            else:
                old = c.execute('SELECT * FROM research_bundles WHERE digest=?', (key,)).fetchone()
            if old:
                return {**json.loads(old['receipt']), 'duplicate': True}
            folder = self.root / key
            stage = Path(tempfile.mkdtemp(prefix='.intake-', dir=self.root))
            try:
                (stage / 'package.zip').write_bytes(blob)
                (stage / 'bundle.json').write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
                for path, data in assets.items():
                    dest = stage / 'assets' / path; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
                # Templates remain candidates. Only an explicit subsequent selection activates them.
                library = {'scope': 'Unverified imported craft evidence; not factual authority or production approval.',
                           'variants': [{**t, 'evidence_bundle': key} for t in b['templates']]}
                (stage / 'style_library.candidate.json').write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")
                receipt = {'bundle_id': b['bundle_id'], 'content_sha256': key,
                           'package_sha256': hashlib.sha256(blob).hexdigest(),
                           'state': 'received_unverified', 'duplicate': False,
                           'sources': len(b['sources']), 'shots': len(b['shots']), 'templates': len(b['templates']),
                           'audio_unreviewed': sum(s['audio_status'] != 'listened' for s in b['sources']),
                           'content_approved': False, 'production_jobs_created': 0, 'requests': []}
                for topic in b['topics']:
                    rid = 'research_' + digest({'bundle': key, 'topic': topic['id']})[:24]
                    c.execute('INSERT INTO research_requests VALUES(?,?,?,?,?)',
                              (rid, b['bundle_id'], topic['id'], 'awaiting_source_verification', json.dumps(topic, ensure_ascii=False)))
                    receipt['requests'].append({'id': rid, 'state': 'awaiting_source_verification'})
                (stage / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
                if folder.exists():
                    # Possible filesystem commit before database rollback/crash: preserve orphan for reconciliation.
                    raise ValueError('unregistered evidence directory exists; reconcile before retry')
                stage.rename(folder)
                c.execute('INSERT INTO research_bundles VALUES(?,?,?,?,?)',
                          (b['bundle_id'], key, str(folder), json.dumps(receipt), time.time()))
            finally:
                if stage.exists(): shutil.rmtree(stage)
        return receipt

    def status(self, bundle_id):
        with self.queue.connect() as c:
            row = c.execute('SELECT receipt FROM research_bundles WHERE id=?', (bundle_id,)).fetchone()
            if not row: raise KeyError(bundle_id)
            result = json.loads(row['receipt'])
            result['requests'] = [dict(x) for x in c.execute('SELECT id,state FROM research_requests WHERE bundle_id=?', (bundle_id,))]
            for request in result['requests']:
                report = self.root / 'processing' / request['id'] / 'result.json'
                if report.exists():
                    processed = json.loads(report.read_text(encoding="utf-8"))
                    request['jobs'] = []
                    for item in processed.get('results', []):
                        jid = item.get('job_id')
                        if not jid: continue
                        counts = dict(c.execute('SELECT status,count(*) FROM tasks WHERE job_id=? GROUP BY status', (jid,)))
                        request['jobs'].append({'id': jid, 'task_counts': counts,
                                                'content_approved': False})
            return result

    def verification_config(self, request_id):
        with self.queue.connect() as c:
            row = c.execute('SELECT * FROM research_requests WHERE id=?', (request_id,)).fetchone()
            if not row: raise KeyError(request_id)
            topic = json.loads(row['payload'])
            return {'seeds': topic['primary_source_urls'], 'discover_links': False,
                    'domains': list(DEFAULT_DOMAINS), 'max_articles': len(topic['primary_source_urls']),
                    'max_age_days': 2}

    def process(self, request_id, production_config, *, collector=None, producer=None):
        """Trusted worker only: verify official sources, then reuse batch dedup/dispatch.

        Configuration (music, model, backend, budget) is owned by the service,
        never supplied by Dot. No call to this method is exposed over HTTP.
        """
        import fcntl
        from .ingest import collect, in_window
        import datetime as dt
        from .batch import produce
        from .core.io import write_json
        collector = collector or collect
        producer = producer or produce
        identifier(request_id)
        # One consumer against this database; includes crash-safe OS lock release.
        with open(self.queue.path + '.research-process.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.queue.connect() as c:
                row = c.execute('SELECT * FROM research_requests WHERE id=?', (request_id,)).fetchone()
                if not row: raise KeyError(request_id)
                if row['state'] in ('enqueued', 'verification_failed', 'attention'):
                    return {'request_id': request_id, 'state': row['state'], 'duplicate': True}
                bundle = c.execute('SELECT path FROM research_bundles WHERE id=?', (row['bundle_id'],)).fetchone()
                c.execute('UPDATE research_requests SET state=? WHERE id=?', ('verifying', request_id))
            folder = self.root / 'processing' / request_id
            folder.mkdir(parents=True, exist_ok=True)
            try:
                # The existing collector caches by source bytes and enforces publication freshness.
                cached = folder / 'verification' / 'catalog.json'
                prior = folder / 'result.json'
                stale = False
                if row['state'] in ('awaiting_backend','awaiting_storyboard') and cached.exists() and prior.exists():
                    catalog = json.loads(cached.read_text(encoding="utf-8"))
                    require(digest(catalog) == json.loads(prior.read_text(encoding="utf-8")).get('catalog_sha256'), 'verified catalog changed')
                    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
                    stale = any(not in_window(t.get('published'), today, 2) for t in catalog.get('topics', []))
                else:
                    catalog = collector(self.verification_config(request_id), folder / 'verification')
                if not catalog.get('topics') or stale:
                    state, results = 'verification_failed', []
                else:
                    config = dict(production_config)
                    # Multiple corroborating URLs in a proposal must not multiply the film count.
                    config['limit'] = 1
                    if config.pop('use_imported_style', False):
                        topic = json.loads(row['payload'])
                        source = json.loads((Path(bundle['path']) / 'style_library.candidate.json').read_text(encoding="utf-8"))
                        source['variants'] = [v for v in source['variants'] if v['id'] == topic['template_id']]
                        selected = folder / 'selected_style.json'
                        write_json(selected, source)
                        config['style_library_file'] = str(selected)
                    results = producer(self.queue, catalog, config, self.root.parent)
                    states = {r.get('state') for r in results}
                    state = ('enqueued' if states and states <= {'enqueued', 'deduplicated'} else
                             'awaiting_backend' if states == {'awaiting_backend'} else
                             'awaiting_storyboard' if states == {'awaiting_storyboard'} else 'attention')
                    if state == 'enqueued':
                        self.adopt(request_id, results)
                result = {'request_id': request_id, 'state': state, 'results': results,
                          'content_approved': False, 'catalog_sha256': digest(catalog),
                          'stale_source_catalog': stale,
                          'verification_catalog': str(folder / 'verification' / 'catalog.json')}
                write_json(folder / 'result.json', result)
            except Exception:
                with self.queue.connect() as c:
                    c.execute('UPDATE research_requests SET state=? WHERE id=?', ('attention', request_id))
                raise
            with self.queue.connect() as c:
                c.execute('UPDATE research_requests SET state=? WHERE id=?', (state, request_id))
            return result

    def adopt(self, request_id, results):
        """New films become visible to the existing singleton production controller."""
        from .controller import Controller
        controller = Controller(self.queue, self.root.parent)
        for result in results:
            jid = result.get('job_id')
            if not jid: continue
            # An existing source may already belong to a different batch, including its repairs.
            owners = controller.items()
            if any(x['current_job'] == jid or json.loads(x['spec']).get('job_id') == jid for x in owners):
                continue
            controller.admit({'id': 'intake_' + request_id + '_' + digest(jid)[:8],
                              'items': [{'id': 'film', 'job_id': jid, 'max_repairs': 0,
                                         'recipes': [], 'research_request_id': request_id}]})
