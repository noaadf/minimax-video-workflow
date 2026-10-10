"""Immutable, budgeted diagnostic revisions; deployment stays with the singleton.

A review file must name every shot, exact output SHA, verdict pass/fail and
sample times including start/middle/end. Failed shots require a concrete patch.
This module never approves visual content or resets an existing task.
"""
import copy
from contextlib import closing
import fcntl
import json
import sqlite3
from pathlib import Path
from workflow.core.io import digest, sha256, write_json


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compile_revision(production_root, source_job, review_path, patches):
    root = Path(production_root).resolve()
    review_path = Path(review_path).resolve()
    review = _read(review_path)
    with closing(sqlite3.connect('file:' + str(root / 'queue.sqlite') + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT manifest FROM jobs WHERE id=?', (source_job,)).fetchone()
        if not row:
            raise ValueError('Source job missing')
        original = json.loads(row[0])
        product = original.get('root_job_id', original['id'])
        current = db.execute('SELECT current_job FROM production_items WHERE item_id=?', (product,)).fetchone()
        tasks = db.execute("SELECT id,status,result FROM tasks WHERE job_id=? AND stage='shot'", (source_job,)).fetchall()
    if not current or review.get('job_id') != source_job:
        raise ValueError('Current identity or review identity missing')
    shots = {s['id']: s for s in original['shots']}
    results = {r[0].rsplit(':', 1)[-1]: (r[1], json.loads(r[2]) if r[2] else {}) for r in tasks}
    verdicts = review.get('shots', {})
    if set(verdicts) != set(shots) or not patches or not set(patches) <= set(shots):
        raise ValueError('Full board review and nonempty known patches required')
    for sid in shots:
        status, result = results.get(sid, (None, {}))
        path = result.get('video_path')
        evidence = verdicts[sid]
        if status != 'succeeded' or not path or sha256(path) != evidence.get('sha256'):
            raise ValueError('Shot not settled or evidence SHA changed: ' + sid)
        if evidence.get('verdict') != ('fail' if sid in patches else 'pass'):
            raise ValueError('Patch/verdict mismatch: ' + sid)
        if not evidence.get('first_middle_last_checked') or not evidence.get('finding'):
            raise ValueError('Actual visual review required: ' + sid)
        if sid in patches:
            patch = patches[sid]
            if set(patch) - {'prompt', 'camera', 'storyboard'} or not patch.get('prompt') or patch['prompt'] == shots[sid].get('prompt'):
                raise ValueError('Concrete action/prompt repair required: ' + sid)
    identity = digest([source_job, sha256(review_path), patches])
    base = root / 'user_repair30_20261002' / 'diagnosed_revisions' / product
    base.mkdir(parents=True, exist_ok=True)
    with (base / 'compile.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        folder = base / identity
        receipt_path = folder / 'compilation.json'
        if receipt_path.exists():
            prior = _read(receipt_path)
            if sha256(prior['manifest_path']) != prior['manifest_sha256']:
                raise ValueError('Compiled manifest changed')
            return prior
        ledger_path = base / 'repair_budget.json'
        ledger = _read(ledger_path) if ledger_path.exists() else {}
        for sid in patches:
            if sid in ledger and ledger[sid] != identity:
                raise ValueError('Diagnostic repair budget exhausted: ' + sid)
        # Reserve before writing: interrupted compilation may replay only this identity.
        ledger.update({sid: identity for sid in patches})
        write_json(ledger_path, ledger)
        folder.mkdir(exist_ok=True)
        plan = copy.deepcopy(original)
        for key in ('approved_story_preflight', 'runtime_review_binding'):
            plan.pop(key, None)
        plan.update(id=product + '-diagnosed-' + identity[:12], parent_job_id=current[0], root_job_id=product)
        for shot in plan['shots']:
            sid = shot['id']
            if sid in patches:
                for key in ('source_path', 'source_sha256', 'source_fit_policy'):
                    shot.pop(key, None)
                shot.update(copy.deepcopy(patches[sid]))
            else:
                path = results[sid][1]['video_path']
                shot.update(source_path=path, source_sha256=sha256(path), source_fit_policy='whole_action')
        plan['diagnostic_revision'] = {'source_job_id': source_job, 'review_path': str(review_path),
            'review_sha256': sha256(review_path), 'repaired_shots': sorted(patches),
            'repair_budget_per_shot': 1, 'identity': identity,
            'old_delivery_hold_preserved': str(root / 'user_repair30_20261002' / 'delivery_holds' / (source_job + '.json'))}
        manifest = folder / 'manifest.json'
        write_json(manifest, plan)
        receipt = {'identity': identity, 'product_id': product, 'source_job_id': source_job,
            'parent_job_id': current[0], 'manifest_path': str(manifest), 'manifest_sha256': sha256(manifest),
            'state': 'requires_new_full_preflight', 'repaired_shots': sorted(patches)}
        write_json(receipt_path, receipt)
        return receipt


def review_and_register(receipt, production_root):
    """Run new full review once and register immutable packet, never submit."""
    from workflow.storyboard_batch import one
    from workflow.storyboard_batch import stage
    from workflow.storyboard_batch import register
    root = Path(production_root).resolve()
    if sha256(receipt['manifest_path']) != receipt['manifest_sha256']:
        raise ValueError('Compiled manifest changed')
    plan = _read(receipt['manifest_path'])
    diagnosis = plan['diagnostic_revision']
    if sha256(diagnosis['review_path']) != diagnosis['review_sha256']:
        raise ValueError('Diagnostic review changed')
    for shot in plan['shots']:
        if shot.get('source_path') and sha256(shot['source_path']) != shot.get('source_sha256'):
            raise ValueError('Reviewed reused shot changed: ' + shot['id'])
    lock_path = root / 'user_repair30_20261002' / 'batch_story_v3' / 'preflight_dispatch.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        reviewed = one(receipt)
    if reviewed['state'] != 'ready_for_staging':
        return reviewed
    with closing(sqlite3.connect('file:' + str(root / 'queue.sqlite') + '?mode=ro', uri=True)) as db:
        active = db.execute("SELECT COUNT(*) FROM tasks WHERE job_id=? AND status='running'", (receipt['source_job_id'],)).fetchone()[0]
        current = db.execute('SELECT current_job FROM production_items WHERE item_id=?', (receipt['product_id'],)).fetchone()
    if active:
        return {'state': 'awaiting_source_settlement', 'manifest_path': receipt['manifest_path']}
    if not current or current[0] not in (receipt['parent_job_id'], receipt['source_job_id']):
        raise ValueError('Current product moved to an unrelated revision')
    packet = stage(reviewed['acceptance_path'], receipt['product_id'], current[0], root)
    # Inherit the exact proven executor and runtime constraints; stage only copies evidence.
    source_requests = [_read(p) for p in (root / 'storyboard_rollout' / 'requests').glob('*/deployment_request.json')]
    sources = [r for r in source_requests if r.get('job_id') == receipt['source_job_id']]
    if len(sources) != 1 or not sources[0].get('backend_file'):
        raise ValueError('Verified source execution request missing or ambiguous')
    execution = {k: v for k, v in sources[0].items() if k == 'backend_file' or k == 'renderer_runtime_path' or k.startswith('required_')}
    if not Path(execution['backend_file']).is_file():
        raise ValueError('Source backend configuration missing')
    packet.update(execution)
    result = register(packet, root)
    if result['state'] == 'isolated':
        # Recover only the known, pre-admission packet omission. Never retry generated jobs.
        request_path = Path(result['path'])
        with (request_path.parent / 'register.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            prior = _read(request_path)
            with closing(sqlite3.connect('file:' + str(root / 'queue.sqlite') + '?mode=ro', uri=True)) as db:
                exists = db.execute('SELECT 1 FROM jobs WHERE id=?', (plan['id'],)).fetchone()
            if exists or prior.get('job_id') or prior.get('error') != "'backend_file'":
                raise ValueError('Isolated request requires independent diagnosis')
            write_json(request_path.parent / 'pre_admission_failure.json', prior)
            write_json(request_path, {**packet, 'state': 'approved', 'product_id': receipt['product_id']})
            write_json(request_path.parent / 'status.json', {'state': 'awaiting_singleton_admission', 'film_ready': False})
            result = {**result, 'state': 'approved', 'recovered_pre_admission_packet': True}

    write_json(Path(receipt['manifest_path']).parent / 'registration.json', result)
    return result


def amend_unreviewed_draft(receipt, patches):
    """Correct a reserved draft before any review/API call; retain prior bytes."""
    manifest = Path(receipt['manifest_path'])
    base = manifest.parent.parent
    with (base / 'compile.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (manifest.parent / 'preflight').exists():
            raise ValueError('Reviewed or attempted revision is immutable')
        if sha256(manifest) != receipt['manifest_sha256'] or set(patches) != set(receipt['repaired_shots']):
            raise ValueError('Draft binding or repair scope changed')
        plan = _read(manifest)
        for shot in plan['shots']:
            if shot['id'] in patches:
                patch = patches[shot['id']]
                if set(patch) - {'prompt', 'camera', 'storyboard'}:
                    raise ValueError('Only diagnosed shot action may be amended')
                shot.update(copy.deepcopy(patch))
        backup = manifest.parent / ('superseded_draft_' + receipt['manifest_sha256'] + '.json')
        backup.write_bytes(manifest.read_bytes())
        write_json(manifest, plan)
        receipt = {**receipt, 'manifest_sha256': sha256(manifest)}
        write_json(manifest.parent / 'compilation.json', receipt)
        return receipt


def compile_timing_revision(production_root, source_job, durations, diagnosis, visual_patches=None):
    """Repair only pre-dispatch natural-timing failures; reuse settled media by SHA.

    This is not a visual pass and does not register or submit. Failed generation,
    unresolved in-flight jobs and changed artifacts are never retried here.
    """
    import math
    from workflow import media
    from workflow.plan import validate
    if not isinstance(diagnosis, str) or not diagnosis.strip():
        raise ValueError('Concrete timing diagnosis required')
    root = Path(production_root).resolve()
    with closing(sqlite3.connect('file:' + str(root / 'queue.sqlite') + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT manifest FROM jobs WHERE id=?', (source_job,)).fetchone()
        if not row: raise ValueError('Source job missing')
        original = json.loads(row[0])
        product = original.get('root_job_id', original['id'])
        current = db.execute('SELECT current_job FROM production_items WHERE item_id=?', (product,)).fetchone()
        rows = db.execute("SELECT id,status,result,error FROM tasks WHERE job_id=? AND stage='shot'", (source_job,)).fetchall()
    if not current or current[0] not in (source_job, original.get('parent_job_id')):
        raise ValueError('Source must be current product job or its exact registered child')
    shots = {s['id']: s for s in original['shots']}
    results = {r[0].rsplit(':', 1)[-1]: (r[1], json.loads(r[2]) if r[2] else {}, r[3]) for r in rows}
    _timing_durations(original, durations, shots, results, math)
    active = [sid for sid, (status, _, _) in results.items() if status in ('running', 'pending', 'leased')]
    if active:
        return {'state':'awaiting_source_settlement','source_job_id':source_job,'running_shots':active}
    visual_patches = visual_patches or {}
    if not set(visual_patches) <= set(shots):raise ValueError('Unknown visual repair shot')
    evidence, failed, visual_evidence = _timing_evidence(shots, results, durations, visual_patches, math, media)
    if not failed: raise ValueError('No timing failure to repair')
    identity=digest([source_job,durations,diagnosis,evidence,visual_patches,visual_evidence])
    base=root/'user_repair30_20261002'/'diagnosed_revisions'/product
    base.mkdir(parents=True,exist_ok=True)
    with (base/'compile.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        folder=base/('timing-'+identity[:24]);receipt_path=folder/'compilation.json'
        if receipt_path.exists():
            prior=_read(receipt_path)
            if sha256(prior['manifest_path']) != prior['manifest_sha256']:raise ValueError('Compiled timing manifest changed')
            return prior
        ledger_path=base/'timing_repair_budget.json';ledger=_read(ledger_path) if ledger_path.exists() else {}
        for sid in failed:
            if sid in ledger and ledger[sid]!=identity:raise ValueError('Timing repair budget exhausted: '+sid)
        visual_ledger_path=base/'repair_budget.json';visual_ledger=_read(visual_ledger_path) if visual_ledger_path.exists() else {}
        for sid in visual_patches:
            if sid in visual_ledger and visual_ledger[sid]!=identity:raise ValueError('Diagnostic repair budget exhausted: '+sid)
        ledger.update({sid:identity for sid in failed});write_json(ledger_path,ledger)
        if visual_patches:
            visual_ledger.update({sid:identity for sid in visual_patches});write_json(visual_ledger_path,visual_ledger)
        plan = _timing_plan(original, product, identity, current, durations, visual_patches, failed, evidence, source_job, diagnosis, visual_evidence)
        validate(plan);folder.mkdir(exist_ok=True);manifest=folder/'manifest.json';write_json(manifest,plan)
        receipt={'identity':identity,'product_id':product,'source_job_id':source_job,'parent_job_id':current[0],'manifest_path':str(manifest),'manifest_sha256':sha256(manifest),'state':'requires_new_full_preflight','repaired_shots':sorted(set(failed)|set(visual_patches)),'pre_dispatch_timing_repairs':failed}
        write_json(receipt_path,receipt);return receipt


def _timing_durations(original, durations, shots, results, math):
    if set(durations) != set(shots) or set(results) != set(shots):
        raise ValueError('Complete shot timing and task set required')
    if any(not math.isfinite(float(v)) or float(v) <= 0 for v in durations.values()):
        raise ValueError('Invalid shot duration')
    if abs(sum(durations.values()) - original['duration_seconds']) > 0.001:
        raise ValueError('Timing repair must preserve whole-film duration')


def _timing_evidence(shots, results, durations, visual_patches, math, media):
    expected = 'execute: Generated action cannot fit this slot naturally; retime storyboard before dispatch'
    evidence = {}; failed = []
    visual_evidence = {}
    for sid, shot in shots.items():
        status, result, error = results[sid]; slot = float(durations[sid])
        if status == 'failed':
            _failed_timing_evidence(sid, shot, status, result, error, slot, expected, failed, evidence, math)
        elif status == 'succeeded':
            path, expected_sha, actual = _settled_timing_clip(sid, result, media)
            if sid in visual_patches:
                _visual_timing_evidence(sid, shot, slot, visual_patches, path, expected_sha, actual, visual_evidence, evidence, status, math)
                continue
            _reused_timing_evidence(sid, shot, slot, result, path, expected_sha, actual, status, evidence)
        else:
            raise ValueError('Source shot not safely settled: ' + sid)
    return (evidence, failed, visual_evidence)


def _failed_timing_evidence(sid, shot, status, result, error, slot, expected, failed, evidence, math):
    if error != expected or result:
        raise ValueError('Not a proven pre-dispatch timing failure: ' + sid)
    old = float(shot['duration_seconds'])
    if max(4, math.ceil(old))/old <= 1.5 or slot == old:
        raise ValueError('Failure does not match diagnosed timing: ' + sid)
    if max(4, math.ceil(slot))/slot > 1.5:
        raise ValueError('New slot still exceeds natural speed: ' + sid)
    failed.append(sid); evidence[sid]={'status':status,'error':error,'new_generation_seconds':max(4,math.ceil(slot))}


def _settled_timing_clip(sid, result, media):
    path = result.get('video_path'); expected_sha = result.get('sha256',{}).get(path)
    if not path or not expected_sha or sha256(path) != expected_sha:
        raise ValueError('Settled media missing or changed: ' + sid)
    actual = media.duration(path)
    return (path, expected_sha, actual)


def _visual_timing_evidence(sid, shot, slot, visual_patches, path, expected_sha, actual, visual_evidence, evidence, status, math):
    patch = visual_patches[sid]
    if set(patch) != {'prompt', 'review_path'} or not patch['prompt'].strip() or patch['prompt'] == shot.get('prompt'):
        raise ValueError('Concrete visual repair prompt and review path required: ' + sid)
    review = _read(patch['review_path']); times = review.get('sample_times', [])
    if (review.get('video_sha256') != expected_sha or review.get('video_path') != path or
        not review.get('major_findings') or review.get('status') != 'reuse_requires_editorial_diagnosis' or
        not times or min(times) > 0.2 or max(times) < actual - 0.3 or
        not any(abs(t-actual/2) < max(0.3,actual/10) for t in times)):
        raise ValueError('Actual bound first/middle/end visual failure evidence required: ' + sid)
    if max(4, math.ceil(slot))/slot > 1.5:raise ValueError('Visual repair slot still too short: ' + sid)
    visual_evidence[sid]={'review_path':str(Path(patch['review_path']).resolve()),'review_sha256':sha256(patch['review_path']),'failed_video_sha256':expected_sha,'findings':review['major_findings']}
    evidence[sid]={'status':status,'failed_video_path':path,'failed_video_sha256':expected_sha,'new_generation_seconds':max(4,math.ceil(slot))}


def _reused_timing_evidence(sid, shot, slot, result, path, expected_sha, actual, status, evidence):
    if not 1/1.5 <= actual/slot <= 1.5:
        raise ValueError('Reused complete clip timing exceeds natural speed: ' + sid)
    timing = result.get('picture_timing', {})
    if shot.get('production_mode') == 'generated_action':
        # Account for the original normalization as well, not just a
        # second speed-up of an already accelerated normalized clip.
        raw_seconds = timing.get('source_seconds')
        if not raw_seconds or timing.get('fit_policy') != 'whole_action':
            raise ValueError('Original complete-action timing evidence missing: ' + sid)
        if not 1/1.5 <= raw_seconds/slot <= 1.5:
            raise ValueError('Cumulative raw action speed exceeds natural bounds: ' + sid)
    evidence[sid]={'status':status,'source_path':path,'source_sha256':expected_sha,'normalized_seconds':actual,'original_timing':timing}


def _timing_plan(original, product, identity, current, durations, visual_patches, failed, evidence, source_job, diagnosis, visual_evidence):
    plan=copy.deepcopy(original)
    for key in ('approved_story_preflight','runtime_review_binding'):plan.pop(key,None)
    plan.update(id=product+'-timing-'+identity[:12],parent_job_id=current[0],root_job_id=product)
    timeline=[];cursor=0.0
    for shot in plan['shots']:
        sid=shot['id'];slot=float(durations[sid]);old=shot['duration_seconds'];shot['duration_seconds']=slot
        shot['duration_reason']='Diagnostic whole-film retiming; continuous narration unchanged; complete source action retained within natural speed bounds'
        if sid in visual_patches:
            for key in ('source_path','source_sha256','source_fit_policy','source_start'):shot.pop(key,None)
            shot['prompt']=visual_patches[sid]['prompt']
        elif sid not in failed:shot.update(source_path=evidence[sid]['source_path'],source_sha256=evidence[sid]['source_sha256'],source_fit_policy='whole_action',source_start=0)
        timeline.append({'shot_id':sid,'old_duration':old,'edit_start':round(cursor,4),'edit_end':round(cursor+slot,4),'duration':slot});cursor+=slot
    plan['diagnostic_timing_revision']={'source_job_id':source_job,'identity':identity,'diagnosis':diagnosis,'failed_pre_dispatch_shots':failed,'visual_repairs':visual_evidence,'evidence':evidence,'timeline':timeline,'visual_approval':False,'requires_new_full_preflight':True}
    return plan
