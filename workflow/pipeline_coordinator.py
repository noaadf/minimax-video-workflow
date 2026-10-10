"""Durable orchestration of immutable storyboard versions, not a video controller.

Adapters own external-call reconciliation. Replaying a phase is never permission
for replaying an unacknowledged provider request. Existing image intents and
preflight checkpoints implement that boundary.
"""
import fcntl
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from workflow.code_identity import code_identity

from workflow.core.io import digest, sha256


def verify_files(value):
    if isinstance(value, dict):
        if 'path' in value and 'sha256' in value:
            if sha256(value['path']) != value['sha256']:
                raise ValueError('Evidence changed: ' + str(value['path']))
        for key, checksum in value.items():
            if key.endswith('_sha256') and key[:-7] + '_path' in value:
                path = Path(value[key[:-7] + '_path'])
                if not checksum or sha256(path) != checksum:
                    raise ValueError('Evidence changed: ' + str(path))
        for child in value.values():
            verify_files(child)
    elif isinstance(value, list):
        for child in value:
            verify_files(child)


class Coordinator:
    def __init__(self, db_path, adapter=None):
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.adapter = adapter or production_step
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS pipeline_versions (
                  id TEXT PRIMARY KEY, product_id TEXT NOT NULL,
                  spec TEXT NOT NULL, phase TEXT NOT NULL, state TEXT NOT NULL,
                  detail TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS pipeline_inbox (path TEXT, checksum TEXT, outcome TEXT, PRIMARY KEY(path,checksum));
                CREATE TABLE IF NOT EXISTS pipeline_events (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT,
                  phase TEXT, state TEXT, detail TEXT, at REAL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, packet_path):
        path = Path(packet_path).resolve()
        packet = json.loads(path.read_text(encoding="utf-8"))
        if packet.get('schema_version') != 1 or not isinstance(packet.get('products'), list):
            raise ValueError('Expected schema_version1/products')
        specs = []
        rejected = []
        for item in packet['products']:
            spec = {**{k:v for k,v in packet.items() if k != 'products'}, **item,
                    'packet_path': str(path), 'packet_sha256': sha256(path)}
            try:
                for key in ('product_id', 'manifest_path', 'manifest_sha256', 'draft_path',
                            'draft_sha256', 'narrative_review_path', 'narrative_review_sha256'):
                    if not spec.get(key):
                        raise ValueError('Missing ' + key)
                if type(spec.get('max_images')) is not int or not 0 <= spec['max_images'] <= 64:
                    raise ValueError('Explicit finite max_images 0..64 required')
                verify_files(spec)
                # Bind actual authored dependencies even when the old manifest did
                # not explicitly carry their digests (voice, source clips, refs).
                dependencies = []
                def collect(value):
                    if isinstance(value, dict):
                        for key, child in value.items():
                            if isinstance(child, str) and key.endswith('_path') and Path(child).is_file():
                                dependencies.append({'path':str(Path(child).resolve()), 'sha256':sha256(child)})
                            elif key == 'reference_images' and isinstance(child, list):
                                for ref in child:
                                    if isinstance(ref, str) and Path(ref).is_file():
                                        dependencies.append({'path':str(Path(ref).resolve()), 'sha256':sha256(ref)})
                            collect(child)
                    elif isinstance(value, list):
                        for child in value:
                            collect(child)
                collect(json.loads(Path(spec['manifest_path']).read_text(encoding="utf-8")))
                spec['authored_dependencies'] = dependencies
                specs.append(spec)
            except Exception as exc:
                rejected.append((spec, str(exc)))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for spec in specs:
                identity = digest(spec)
                # A new packet filename/budget must never buy another provider
                # attempt for the SAME authored version after isolation.
                try:
                    prior_versions = db.execute('SELECT id,spec FROM pipeline_versions WHERE product_id=?', (spec['product_id'],)).fetchall()
                    for prior in prior_versions:
                        if json.loads(prior['spec']).get('manifest_sha256') == spec['manifest_sha256'] and prior['id'] != identity:
                            raise ValueError('Existing manifest version must retain original ledger: ' + prior['id'])
                    # New versions do not silently replace an active version.
                    existing = db.execute("SELECT id FROM pipeline_versions WHERE product_id=? AND id<>? AND state NOT LIKE 'isolated_%' AND state NOT IN ('invalidated','delivered')", (spec['product_id'], identity)).fetchone()
                    if existing:
                        raise ValueError('Active product version exists: ' + existing['id'])
                except ValueError as exc:
                    rejected.append((spec, str(exc)))
                    continue
                db.execute('INSERT OR IGNORE INTO pipeline_versions VALUES(?,?,?,?,?,?,?)',
                           (identity, spec['product_id'], json.dumps(spec), 'assets', 'pending', '{}', time.time()))
            for spec, reason in rejected:
                identity = digest(spec)
                db.execute('INSERT OR IGNORE INTO pipeline_versions VALUES(?,?,?,?,?,?,?)',
                           (identity, str(spec.get('product_id', 'unknown')), json.dumps(spec),
                            'assets', 'isolated_intake', json.dumps({'reason':reason}), time.time()))
        return list(dict.fromkeys([digest(spec) for spec in specs] + [digest(spec) for spec, _ in rejected]))

    def discover(self, folder):
        """Consume immutable packets once; malformed products do not starve peers."""
        results = []
        for path in sorted(Path(folder).glob('*.json')):
            checksum = sha256(path)
            with self.connect() as db:
                seen = db.execute('SELECT 1 FROM pipeline_inbox WHERE path=? AND checksum=?', (str(path.resolve()), checksum)).fetchone()
            if seen:
                continue
            try:
                result = {'state':'registered', 'ids':self.register(path)}
            except Exception as exc:
                result = {'state':'isolated_intake', 'reason':str(exc)}
            with self.connect() as db:
                db.execute('INSERT OR IGNORE INTO pipeline_inbox VALUES(?,?,?)', (str(path.resolve()), checksum, json.dumps(result)))
            results.append({'path':str(path), **result})
        return results

    def recover_unsubmitted_assets(self, identity, expected_error, diagnosis_path, diagnosis_sha256):
        """Explicit diagnosed local-code repair, never an API retry authorization."""
        with self.path.with_suffix('.service.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            row = next(r for r in self.status() if r['id'] == identity)
            if row['phase'] != 'assets' or row['state'] != 'isolated_error' or row['detail'] != expected_error:
                raise ValueError('Recovery target no longer matches diagnosed local failure')
            verify_files(row['spec'])
            diagnosis = {'path': str(Path(diagnosis_path).resolve()), 'sha256': diagnosis_sha256}
            verify_files(diagnosis)
            work = self.path.parent / 'versions' / identity
            attempts = work / 'image_attempts'
            if attempts.exists() and any(attempts.rglob('*')):
                raise ValueError('Provider attempt evidence exists; automatic recovery forbidden')
            for path in (work / 'images').glob('*.json'):
                task = json.loads(path.read_text(encoding="utf-8"))
                if task.get('state') != 'pending' or any(k in task for k in ('claim_token','claimed_at','image_path','external_executor')):
                    raise ValueError('Image task already claimed or completed; reconcile separately')
            if (work / 'derived_sources').exists():
                raise ValueError('Source builder attempt exists; reconcile separately')
            self.record(identity, 'assets', 'pending', {'technical_recovery': diagnosis,
                        'prior_error': expected_error, 'same_ledger': True, 'provider_submission_evidence': 'none'})
            return identity

    def recover_local_downstream(self, identity, diagnosis_path, diagnosis_sha256, source_resolution=None):
        """Resume only two diagnosed local binding failures; never failed API stages."""
        with self.path.with_suffix('.service.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            row = next(r for r in self.status() if r['id'] == identity)
            reason = row['detail'].get('reason', '')
            if row['phase'] != 'downstream' or row['state'] != 'isolated_error' or row['detail'].get('error_type') != 'ValueError':
                raise ValueError('Not a diagnosed local downstream failure')
            if reason != 'Candidate source differs from parent' and not reason.startswith('Incomplete bound board:'):
                raise ValueError('Recovery does not permit this failure')
            if (reason == 'Candidate source differs from parent') != bool(source_resolution):
                raise ValueError('Source identity recovery requires explicit resolution')
            verify_files(row['spec'])
            diagnosis = {'path': str(Path(diagnosis_path).resolve()), 'sha256': diagnosis_sha256}
            verify_files(diagnosis)
            work = self.path.parent / 'versions' / identity
            for path in (work / 'downstream').rglob('*.checkpoint.json'):
                record = json.loads(path.read_text(encoding="utf-8"))
                allowed = path.name == ('stage.checkpoint.json' if source_resolution else 'bind.checkpoint.json')
                if record['state'] != 'succeeded' and not (allowed and record['state'] == 'failed'):
                    raise ValueError('Nonlocal or unfinished checkpoint cannot be retried: ' + str(path))
            recovery = {'diagnosis': diagnosis, 'prior_error': row['detail'], 'source_identity_resolution': source_resolution}
            verify_files(recovery)
            path = work / 'local_recovery.json'
            if path.exists() and json.loads(path.read_text(encoding="utf-8")) != recovery:
                raise ValueError('Existing recovery differs')
            from workflow.core.io import write_json
            write_json(path, recovery)
            self.record(identity, 'downstream', 'pending', {'recovery_path':str(path), 'recovery_sha256':sha256(path), 'same_ledger':True})
            return identity

    def status(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM pipeline_versions ORDER BY updated,id').fetchall()
        return [{**dict(row), 'spec':json.loads(row['spec']), 'detail':json.loads(row['detail'])} for row in rows]

    def record(self, identity, phase, state, detail):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            values = (phase, state, json.dumps(detail), time.time())
            db.execute('UPDATE pipeline_versions SET phase=?,state=?,detail=?,updated=? WHERE id=?', (*values, identity))
            db.execute('INSERT INTO pipeline_events(version_id,phase,state,detail,at) VALUES(?,?,?,?,?)', (identity, *values))

    def _advance(self, row):
        identity, phase = row['id'], row['phase']
        try:
            # Terminal entries still invalidate when their evidence is altered.
            verify_files(row['spec'])
            verify_files(row['detail'])
        except Exception as exc:
            self.record(identity, phase, 'invalidated', {'reason':str(exc)})
            return
        if row['state'].startswith('isolated_') or row['state'] in ('invalidated', 'delivered'):
            return
        self.record(identity, phase, 'running', row['detail'])
        try:
            work = self.path.parent / 'versions' / identity
            work.mkdir(parents=True, exist_ok=True)
            result = self.adapter(row['spec'], phase, work)
            if not isinstance(result, dict) or not result.get('state'):
                raise ValueError('Adapter returned no state')
            next_phase = result.get('phase', phase)
            if next_phase not in ('assets', 'downstream', 'delivery'):
                raise ValueError('Invalid phase')
            self.record(identity, next_phase, result['state'], result.get('detail', {}))
        except Exception as exc:
            self.record(identity, phase, 'isolated_error', {'error_type':type(exc).__name__, 'reason':str(exc)})

    def tick(self, workers=2):
        if workers not in (1, 2):
            raise ValueError('workers must be1 or2')
        with self.path.with_suffix('.service.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(self._advance, self.status()))
        return self.status()


def infrastructure_ready(spec, plan):
    """Check recoverable local prerequisites before any chargeable asset work."""
    from workflow.storyboard_downstream import runtime_ready, Paused
    from workflow import media, storyboard, first_frame, review
    project = Path(__file__).resolve().parents[1]
    media_sha = code_identity(project / 'workflow/media.py')
    review_sha = code_identity(project / 'workflow/review.py')
    if media.MEDIA_CODE_SHA256 != media_sha or review.REVIEW_CODE_SHA256 != review_sha:
        raise Paused('awaiting_runtime', {'reason': 'Coordinator imported code is stale; controlled coordinator reload required',
                     'required_media_sha256': media_sha, 'required_review_sha256': review_sha})
    packet = {'renderer_runtime_path': spec['renderer_runtime_path'],
              'required_media_sha256': media_sha, 'required_media_version': 'whole_action_v1',
              'required_story_review_version': 'shot-review-v3-explicit-endpoint-evidence',
              'required_story_preflight_version': storyboard.PREFLIGHT_VERSION}
    if first_frame.enabled(plan):
        packet['required_first_frame_runtime'] = first_frame.runtime_requirements()
    try:
        runtime_ready(packet)
        runtime = json.loads(Path(spec['renderer_runtime_path']).read_text(encoding="utf-8"))
        if runtime.get('loaded_review_sha256') != review_sha:
            raise Paused('awaiting_runtime', {'reason': 'Renderer review code differs from current code'})
    except (Paused, FileNotFoundError, ProcessLookupError) as exc:
        raise Paused('awaiting_runtime', {'reason': str(exc),
                     'runtime_path': spec['renderer_runtime_path'],
                     'required_media_sha256': media_sha, 'required_review_sha256': review_sha}) from exc


def _required_images(plan):
    from workflow.first_frame import reference_roles
    count = 0
    for shot in plan['shots']:
        if shot.get('source_path') or shot.get('production_mode') == 'deterministic_graphic':
            continue
        for index, role in enumerate(reference_roles(plan, shot)):
            refs = shot.get('reference_images', [])
            if len(refs) > index and Path(refs[index]).is_file():
                continue
            if not shot.get('keyframe_prompts', {}).get(role):
                return {'state':'awaiting_authored_prompts', 'detail':{'shot':shot['id'], 'role':role}}
            count += 1
    return count


def _generate_images(spec, plan, item, work, count):
    from workflow.storyboard_image_jobs import ImageJobs, _canvas
    from workflow.external_image_executor import ExternalImages, payload_for
    jobs = ImageJobs(item['image_jobs_dir'])
    jobs.add(spec['manifest_path'], spec['product_id'])
    # Validate every provider payload before the first chargeable request.
    for path in jobs.folder.glob('*.json'):
        payload_for({**json.loads(path.read_text(encoding="utf-8")), **_canvas(plan)})
    if count:
        executor = ExternalImages(jobs.folder, work / 'image_attempts',
                    {spec['manifest_sha256']:spec['narrative_review_path']},
                    spec.get('image_config', 'workflow/config/llm_minimax.json'))
        executor.run(limit=count, workers=1, product=spec['product_id'])
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in jobs.folder.glob('*.json')]
    bad = [r for r in rows if r['state'] != 'completed']
    if bad:
        return {'state':'isolated_images', 'detail':{'tasks':[{'id':r['id'], 'state':r['state']} for r in bad]}}
    return rows


def _verified_delivery(spec, work, request, root, entry):
    from workflow.core.io import write_json
    from workflow.repair_delivery import validate_product
    from workflow.queue import Queue
    driver = json.loads((root / 'user_repair30_20261002' / 'driver_status.json').read_text(encoding="utf-8"))
    current = driver.get('items', {}).get(entry['request_id'])
    if not current or current.get('new_job_id') != request['job_id']:
        raise ValueError('Delivery does not match current repair version')
    verified = validate_product(Queue(root / 'queue.sqlite'), current)
    if entry.get('video_sha256') != verified['video_sha256'] or entry.get('files', {}).get('video.mp4', {}).get('sha256') != verified['video_sha256']:
        raise ValueError('Export differs from strictly verified video')
    verify_files(entry['files'])
    # Freeze this entry, not the mutable whole-batch index.
    receipt = work / 'verified_delivery.json'
    write_json(receipt, entry)
    evidence = []
    for key, checksum in entry.get('evidence', {}).items():
        if key.endswith('_sha256') and key[:-7] in entry['evidence']:
            evidence.append({'path':entry['evidence'][key[:-7]], 'sha256':checksum})
    verify_files(evidence)
    return {'state':'delivered', 'detail':{'receipt_path':str(receipt),
            'receipt_sha256':sha256(receipt), 'files':entry['files'],
            'evidence':evidence, 'job_id':request['job_id']}}


def _produce_assets(spec, plan, item, work):
    from workflow.storyboard_downstream import Paused, validate_source_timing
    from workflow.plan import validate
    validate(plan)
    from workflow.coordinator_sources import validate_builders, build_sources
    validate_builders(spec, plan)
    validate_source_timing({**plan, 'shots': [s for s in plan['shots'] if not s.get('source_path') or Path(s['source_path']).is_file()]})
    missing_sources = [s['id'] for s in plan['shots'] if s.get('production_mode') == 'deterministic_graphic' and (not s.get('source_path') or not Path(s['source_path']).is_file())]
    count = _required_images(plan)
    if isinstance(count, dict):
        return count
    if count > spec['max_images']:
        return {'state':'isolated_budget', 'detail':{'required_images':count, 'max_images':spec['max_images']}}
    rows = _generate_images(spec, plan, item, work, count)
    if isinstance(rows, dict):
        return rows
    try:
        build_sources(spec, plan, rows, work)
    except Paused as exc:
        return {'state': exc.state, 'detail': exc.detail}
    missing_sources = [s['id'] for s in plan['shots'] if (s.get('source_path') and not Path(s['source_path']).is_file()) or (s.get('production_mode') == 'deterministic_graphic' and not s.get('source_path'))]
    if missing_sources:
        return {'state':'awaiting_source_material', 'detail':{'shots':missing_sources, 'image_tasks_completed':len(rows), 'image_jobs_dir':item['image_jobs_dir']}}
    validate_source_timing(plan)
    return {'state':'pending', 'phase':'downstream'}


def _produce_downstream(spec, plan, item, work):
    from workflow.core.io import write_json
    from workflow.storyboard_downstream import run_product, Paused
    config = dict(spec)
    recovery_path = work / 'local_recovery.json'
    if recovery_path.exists():
        recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
        verify_files(recovery)
        if recovery.get('source_identity_resolution'):
            config['source_identity_resolution'] = recovery['source_identity_resolution']
    try:
        from workflow.coordinator_sources import prepare_source_candidate
        tasks = [json.loads(p.read_text(encoding="utf-8")) for p in (work / 'images').glob('*.json')]
        item = prepare_source_candidate(item, plan, tasks, work)
        result = run_product(item, config, work / 'downstream', 'register')
    except Paused as exc:
        return {'state':exc.state, 'detail':exc.detail}
    if result['state'] == 'awaiting_singleton_admission':
        write_json(work / 'registration.json', result)
        return {'state':'awaiting_singleton_admission', 'phase':'delivery', 'detail':result}
    return {'state':result['state'], 'detail':result}


def production_step(spec, phase, work):
    """Use existing verified interfaces; never directly submit video jobs."""
    from workflow.narrative_gate import require_review
    from workflow.storyboard_downstream import Paused
    plan = json.loads(Path(spec['manifest_path']).read_text(encoding="utf-8"))
    require_review(spec['manifest_path'], spec['narrative_review_path'])
    source_receipts = work / 'derived_sources.json'
    if source_receipts.exists():
        verify_files(json.loads(source_receipts.read_text(encoding="utf-8")))
    for context in spec.get('audit_context', []):
        evidence = json.loads(Path(context['path']).read_text(encoding="utf-8"))
        if evidence.get('blocks_registration') or evidence.get('status', '').startswith('isolated'):
            return {'state':'isolated_content', 'detail':{'reason':'Unresolved content finding', 'evidence':context}}
    if phase in ('assets', 'downstream'):
        try:
            infrastructure_ready(spec, plan)
        except Paused as exc:
            return {'state': exc.state, 'detail': exc.detail}
    # Each version owns its job ledger: old candidates never enter this run.
    item = {**spec, 'image_jobs_dir':str(work / 'images')}
    if phase == 'assets':
        return _produce_assets(spec, plan, item, work)
    if phase == 'downstream':
        return _produce_downstream(spec, plan, item, work)
    return _produce_delivery(spec, work)


def _produce_delivery(spec, work):
    registration = json.loads((work / 'registration.json').read_text(encoding="utf-8"))
    request = json.loads(Path(registration['registration']['path']).read_text(encoding="utf-8"))
    if request['state'] == 'isolated':
        return {'state':'isolated_production', 'detail':request}
    # A rollout's 'completed' means film review required, NOT user delivery.
    root = Path(spec['production_root'])
    index = root / 'user_repair30_20261002' / 'delivery_index.json'
    if index.exists():
        entries = json.loads(index.read_text(encoding="utf-8")).get('items', [])
        if isinstance(entries, dict):
            entries = list(entries.values())
        for entry in entries:
            if entry.get('product_id') == spec['product_id'] and entry.get('version_job_id') == request.get('job_id'):
                return _verified_delivery(spec, work, request, root, entry)
    return delivery_wait(request, registration['registration']['path'])


def delivery_wait(request, request_path):
    """Expose the exact held gate without retrying generation or clearing review."""
    state = request['state']
    state = 'awaiting_film_acceptance' if state == 'completed' else state if state.startswith('awaiting_') else 'awaiting_' + state
    detail = {'job_id': request.get('job_id'), 'request_path': str(request_path),
              'reason': request.get('reason')}
    if state == 'awaiting_quality_review':
        diagnostic = Path(request_path).parent / 'quality_gate_diagnostic.json'
        if not diagnostic.is_file():
            detail.update(reason='quality_gate_diagnostic_missing', next_action='reconcile_quality_evidence')
        else:
            report = json.loads(diagnostic.read_text(encoding="utf-8"))
            manifest = Path(request['manifest_path'])
            if report.get('manifest_sha256') != request.get('manifest_sha256') or sha256(manifest) != request['manifest_sha256']:
                detail.update(reason='quality_gate_diagnostic_stale', next_action='reconcile_quality_evidence')
            else:
                detail.update(diagnostic_path=str(diagnostic), diagnostic_sha256=sha256(diagnostic),
                              blockers=report['policy']['blockers'], advisories=report['policy'].get('advisories', []),
                              next_action='review_bound_film_evidence', automatic_regeneration_allowed=False)
    return {'state': state, 'detail': detail}
