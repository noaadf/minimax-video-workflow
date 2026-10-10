"""Strict, recoverable completion and delivery writeback for the singleton owner.

No generation or review is performed here. Existing review validators remain the
only authority. A filesystem interruption can be replayed without another audit,
queue submission, completion event, or product version.
"""

import fcntl
import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .core.io import sha256, write_json
from .delivery_policy import store_product_version
from .narrative_gate import require_review, validate_contract
from .queue import Queue, begin_immediate
from .repair import job_bundle
from .sample_coverage import covers_shot

class _TransactionView:
    def __init__(self, connection, path):
        self.connection = connection
        self.path = path

    @contextmanager
    def connect(self):
        # The caller owns commit/rollback; nested validators only read.
        yield self.connection


def _owner(queue, descriptor):
    path = Path(queue.path + '.controller-service.lock')
    if descriptor is None or os.fstat(descriptor).st_ino != path.stat().st_ino:
        raise ValueError('Existing singleton owner lock required')
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def complete(queue, root, item, audit_path, owner_lock_fd):
    """Return completed item only after strict validation, card and export writeback.

    `root` is the repository directory. Call instead of the old inline completion
    SQL, including on replay after a filesystem interruption. Persist the returned
    item in driver_status. Never mark a caught failure completed.
    """
    _owner(queue, owner_lock_fd)
    root = Path(root).resolve()
    folder = root / 'output/production/user_repair30_20261002'
    destination = folder
    destination.mkdir(parents=True, exist_ok=True)
    if Path(queue.path).resolve() != root / 'output/production/queue.sqlite':
        raise ValueError('Queue does not belong to delivery workspace')
    if item.get('state') not in ('auditing', 'completed'):
        raise ValueError('Repair must have reached final audit')
    plan, results = job_bundle(queue, item['new_job_id'])
    export = results['export']
    candidate = {**item, 'state': 'completed', 'audit_path': str(Path(audit_path).resolve()),
                 'new_video_sha256': sha256(export['video_path']),
                 'export_dir': export['export_dir']}
    with (destination / '.refresh.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with queue.connect() as connection:
            begin_immediate(connection)
            request = connection.execute('SELECT * FROM user_repair_requests WHERE id=?',
                                         (item['request_id'],)).fetchone()
            if not request or request['job_id'] != item['job_id'] or request['status'] not in ('processing', 'completed'):
                raise ValueError('Repair request/source version is not active')
            if sha256(request['video_path']) != request['video_sha256']:
                raise ValueError('Original rejected version changed')
            # Temporary within this transaction only. Other readers never observe
            # completion if any strict media/content/audio validation below fails.
            connection.execute("UPDATE user_repair_requests SET status='completed' WHERE id=?", (item['request_id'],))
            verified = validate_product(_TransactionView(connection, queue.path), candidate)
            prior = connection.execute("SELECT detail FROM events WHERE task_id=? AND event='user_repair_completed'",
                                       (item['new_job_id'],)).fetchall()
            replay = any(json.loads(row['detail']).get('request_id') == item['request_id'] for row in prior)
            detail = {'request_id': item['request_id'], 'new_job_id': item['new_job_id'],
                      'previous_job_id': item['job_id'], 'video_sha256': candidate['new_video_sha256'],
                      'full_shot_audit': candidate['audit_path'], 'evidence': verified['evidence'],
                      'completed_item': candidate, 'publication_approved': False}
            if not replay:
                changed = connection.execute("UPDATE production_items SET state='ready_for_review',detail=?,updated=? WHERE item_id=? AND current_job=?",
                                             (json.dumps(detail), time.time(), item['product_id'], item['new_job_id'])).rowcount
                if changed != 1:
                    raise ValueError('Original product card changed')
                queue.event(connection, item['new_job_id'], 'user_repair_completed', json.dumps(detail))
        # Journal precedes export/index: restart picks up committed completion even
        # if driver_status was not saved. Source and prior exports remain intact.
        journal = folder / 'delivery_commits' / (item['new_job_id'] + '.json')
        write_json(journal, candidate)
        _copy_delivery(queue, root, verified, candidate)
        _refresh_index(queue, root, folder, candidate)
    return candidate


def _copy_delivery(queue, root, verified, item):
    """Keep the existing validator authoritative; store only its bound assets."""
    path = Path(item['manifest_path'])
    expected = verified.get('evidence', {}).get('prepared_manifest_sha256')
    if expected and sha256(path) != expected:
        raise ValueError('Prepared manifest changed after validation')
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if expected and sha256(path) != expected:
        raise ValueError('Prepared manifest changed during snapshot')
    with queue.connect() as connection:
        row = connection.execute('SELECT * FROM user_repair_requests WHERE id=?',
                                 (item['request_id'],)).fetchone()
    reason = dict(row).get('note', '') if row else ''
    return store_product_version(root, verified, manifest, repair_reason=reason)


def _refresh_index(queue, root, folder, current):
    state_path = folder / 'driver_status.json'
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    candidates = {i['product_id']: i for i in state.get('items', {}).values() if i.get('state') == 'completed'}
    for path in sorted((folder / 'delivery_commits').glob('*.json')):
        entry = json.loads(path.read_text(encoding="utf-8"))
        with queue.connect() as connection:
            row = connection.execute('SELECT current_job FROM production_items WHERE item_id=?', (entry['product_id'],)).fetchone()
        if row and row['current_job'] == entry.get('new_job_id'):
            candidates[entry['product_id']] = entry
    candidates[current['product_id']] = current
    verified, withheld = [], []
    for product, entry in sorted(candidates.items()):
        try:
            verified.append(_copy_delivery(queue, root, validate_product(queue, entry), entry))
        except (ValueError, OSError, KeyError) as exc:
            withheld.append({'product_id': product, 'version_job_id': entry.get('new_job_id'), 'reason': str(exc)[:500]})
    if current['product_id'] not in {v['product_id'] for v in verified}:
        raise ValueError('Current completion failed delivery revalidation')
    index = {'updated_at': time.time(), 'count': len(verified), 'target': 30,
             'items': verified, 'withheld': withheld,
             'scope': 'Completed current user repairs only; no historical-ready fallback; not published'}
    write_json(folder / 'delivery_index.json', index)


"""Export only verified completion of the user's current repair requests."""
ROOT=Path(__file__).resolve().parents[1]


class NotDeliverable(ValueError):pass


def require_quality(plan,quality,*,dense_audit=None,video=None):
    """Read-only compatibility for old strict results lacking delivery_policy."""
    from workflow.delivery_policy import resolve
    policy=resolve(quality,plan)
    if dense_audit is not None:
        from workflow.dense_delivery_audit import validate as validate_dense_audit
        dense=validate_dense_audit(dense_audit,plan,video)
        if plan.get('quality_profile')=='throughput_v1':
            policy['blockers']=[b for b in policy['blockers'] if b.get('category')!='story_action_unverified']
            policy['advisories'].extend(dense['limitations'])
            policy['dense_review_evidence']=dense
            policy['ready_for_delivery']=not policy['blockers']
    if quality.get('passed') is not True or policy.get('ready_for_delivery') is not True:
        raise NotDeliverable('technical/content quality not ready')
    return policy

def _require_shot_samples(evidence, results, plan):
    for e in evidence:
        actual=results[e['shot_id']]['video_path']
        if Path(e['video_path']).resolve()!=Path(actual).resolve() or e.get('source_sha256')!=sha256(actual):raise NotDeliverable('audited shot/version SHA mismatch')
        times=e.get('sample_seconds',[]);length=next(s['duration_seconds'] for s in plan['shots'] if s['id']==e['shot_id'])
        if not covers_shot(times,length):raise NotDeliverable('first/middle/last samples missing or misaligned')


def _require_product_binding(item, request, plan, current_job, verified_ancestors):
    if plan.get('id')!=item.get('new_job_id') or current_job!=item.get('new_job_id'):raise NotDeliverable('repair is not current product version')
    if plan.get('root_job_id')!=item.get('product_id') or (plan.get('parent_job_id')!=item.get('job_id') and item.get('job_id') not in verified_ancestors) or plan.get('user_repair_request_id')!=item.get('request_id'):raise NotDeliverable('manifest/request/product identity mismatch')
    if plan['id']==request['job_id']:raise NotDeliverable('old version cannot satisfy new repair')


def _audit_flags(rows, triage):
    return [r['shot_id'] for r in rows
            if triage.get(r['shot_id'])!='minor_only_no_regeneration'
            and (r.get('verdict')!='pass' or r.get('confidence',0)<.85
                 or triage.get(r['shot_id'])=='material_repair_required'
                 or any(f.get('severity') in ('medium','high') for f in r.get('findings',[])))]


def require_binding(item,request,plan,current_job,verified_ancestors=()):
    if item.get('state')!='completed':raise NotDeliverable('user repair not completed')
    if not request or request.get('status')!='completed':raise NotDeliverable('repair request not completed')
    if request.get('id')!=item.get('request_id') or request.get('job_id')!=item.get('job_id'):raise NotDeliverable('request/source version mismatch')
    _require_product_binding(item, request, plan, current_job, verified_ancestors)

def verified_ancestry(q,plan,original_job,product):
    """Allow intermediate repair versions only along actual stored parent edges."""
    parent=plan.get('parent_job_id');seen={plan.get('id')};ancestors=[]
    with q.connect() as c:
        while parent:
            if parent in seen:raise NotDeliverable('cyclic repair ancestry')
            seen.add(parent)
            row=c.execute('select manifest from jobs where id=?',(parent,)).fetchone()
            if row is None:raise NotDeliverable('repair ancestor absent')
            ancestor=json.loads(row['manifest'])
            if ancestor.get('id')!=parent or ancestor.get('root_job_id',ancestor.get('id'))!=product:raise NotDeliverable('repair ancestor belongs to another product')
            ancestors.append(parent)
            if parent==original_job:return ancestors
            parent=ancestor.get('parent_job_id')
    raise NotDeliverable('original repair version is not an ancestor')


def _require_authored_content(plan, approved):
    # Runtime paths and backend receipts may differ, authored content must not.
    for field in ('facts','narration','narrative_contract','duration_seconds','comment_prompt','continuity_bible','visual_continuity','pacing_policy','postproduction_labels','repair_round','color_policy','framing_policy','visual_style_policy','voice'):
        if plan.get(field)!=approved.get(field):raise NotDeliverable('authored content changed after narrative review: '+field)
    if [(s['id'],s['prompt'],s['duration_seconds'],s.get('duration_reason')) for s in plan['shots']]!=[(s['id'],s['prompt'],s['duration_seconds'],s.get('duration_reason')) for s in approved['shots']]:raise NotDeliverable('shots changed after narrative review')


def _require_full_shot_audit(audit, audit_path, plan, results, jid, item):
    if audit.get('job_id')!=jid or audit.get('product_id')!=item['product_id']:raise NotDeliverable('full-shot audit belongs to another version')
    if audit.get('kind')=='dense_postproduction_v1':
        pass  # Validated together with the quality gate below, including candidate SHA.
    else:
        flags=audit_file_decision(audit_path,[s['id'] for s in plan['shots']])
        # Owner decision 2026-10-07 ("扔掉审查者"): the wrap-up auditor's verdicts
        # (pass, findings, or failure to run) are recorded, never gating; technical
        # delivery gates elsewhere stay.
        if os.environ.get('WORKFLOW_NARRATIVE_FORMAT_ONLY')=='1':
            audit['format_waiver']='auditor verdicts recorded, not gating (owner 2026-10-07)'
            return
        if flags!=[]:raise NotDeliverable('complete full-shot review has not passed')
        if not audit.get('receipt',{}).get('model') or not isinstance(audit.get('receipt',{}).get('usage'),dict):raise NotDeliverable('full-shot model receipt missing')
        pages=audit.get('review_pages',[])
        if not pages or any(sha256(page['path'])!=page['sha256'] for page in pages):raise NotDeliverable('full-shot review pages missing or changed')
        evidence=audit.get('evidence',[])
        if len(evidence)!=len(plan['shots']) or {e.get('shot_id') for e in evidence}!={s['id'] for s in plan['shots']}:raise NotDeliverable('missing per-shot audit evidence')
        _require_shot_samples(evidence, results, plan)

def _require_audio(mix, mix_path, item, results, plan):
    if mix.get('policy')=='locked_approved_audio':
        from workflow.locked_audio_evidence import validate as validate_locked_audio
        report=item.get('locked_audio_report_path')
        if not report:raise NotDeliverable('locked audio source report missing')
        validate_locked_audio(mix_path,report,results['export']['video_path'],plan)
    else:
        if mix.get('source_audio_in_mix') is not False or mix.get('curated_effects')!=[] or mix.get('policy',{}).get('mode')!='independent_voice_continuous_music':raise NotDeliverable('clean audio not evidenced')
        for field,stage in (('voice','voice'),('music','music')):
            audio=results[stage]['audio_path']
            if Path(mix[field]['path']).resolve()!=Path(audio).resolve() or mix[field]['sha256']!=sha256(audio):raise NotDeliverable('clean audio stem SHA mismatch')


def validate_product(q,item):
    jid=item['new_job_id']
    holds=Path(q.path).parent/'user_repair30_20261002/delivery_holds'/ (jid+'.json') if hasattr(q,'path') else ROOT/'output/production/user_repair30_20261002/delivery_holds'/(jid+'.json')
    if holds.exists() and json.loads(holds.read_text(encoding="utf-8")).get('status')=='blocked':raise NotDeliverable('Verified manual defect blocks delivery: '+str(holds))
    with q.connect() as c:
        request=c.execute('select * from user_repair_requests where id=?',(item['request_id'],)).fetchone()
        row=c.execute('select current_job from production_items where item_id=?',(item['product_id'],)).fetchone()
        source=c.execute('select manifest from jobs where id=?',(jid,)).fetchone()
    if source is None:raise NotDeliverable('new version is absent')
    stored_plan=json.loads(source['manifest'])
    ancestors=verified_ancestry(q,stored_plan,item['job_id'],item['product_id'])
    require_binding(item,dict(request) if request else None,stored_plan,row['current_job'] if row else None,ancestors)
    plan,results=job_bundle(q,jid)  # Includes every task success and recorded artifact hashes.
    prepared=Path(item['manifest_path']);review=prepared.with_suffix('.narrative_review.json');require_review(prepared,review)
    approved=json.loads(prepared.read_text(encoding="utf-8"));validate_contract(plan)
    _require_authored_content(plan, approved)
    audit_path=Path(item['audit_path']);audit=json.loads(audit_path.read_text(encoding="utf-8"))
    _require_full_shot_audit(audit, audit_path, plan, results, jid, item)
    quality=results['quality']
    delivery_policy=require_quality(plan,quality,dense_audit=audit if audit.get('kind')=='dense_postproduction_v1' else None,video=results['export']['video_path'])
    mix_path=Path(results['compose']['audio_mix_path']);mix=json.loads(mix_path.read_text(encoding="utf-8"))
    _require_audio(mix, mix_path, item, results, plan)
    export=results['export'];checksum=sha256(export['video_path'])
    if checksum!=item.get('new_video_sha256'):raise NotDeliverable('completed export SHA mismatch')
    return {'product_id':item['product_id'],'version_job_id':jid,'request_id':item['request_id'],'video_sha256':checksum,'title':plan.get('title',item['product_id']),'sources':{'video.mp4':export['video_path'],'cover.png':export['cover_path'],'publication.txt':export['publication_path']},'evidence':{'full_shot_audit':str(audit_path),'full_shot_audit_sha256':sha256(audit_path),'narrative_review':str(review),'narrative_review_sha256':sha256(review),'prepared_manifest_sha256':sha256(prepared),'audio_mix':str(mix_path),'audio_mix_sha256':sha256(mix_path)},'delivery_policy':delivery_policy,'system_ready':True,'publication_approved':False}

def copy_delivery(verified,destination):
    product=verified['product_id']
    if Path(product).name!=product or product in ('.','..'):raise NotDeliverable('unsafe product identifier')
    target=destination/product;receipt_path=target/'delivery_receipt.json'
    files={name:{'path':str((target/name).resolve()),'sha256':sha256(src)} for name,src in verified['sources'].items()}
    record={k:v for k,v in verified.items() if k!='sources'};record.update(files=files,exported_at=time.time())
    if receipt_path.exists():
        old=json.loads(receipt_path.read_text(encoding="utf-8"))
        if old.get('version_job_id')==record['version_job_id'] and old.get('video_sha256')==record['video_sha256'] and all((target/name).exists() and sha256(target/name)==meta['sha256'] for name,meta in files.items()):return old
    staging=destination/('.staging-'+uuid.uuid4().hex);staging.mkdir()
    try:
        for name,src in verified['sources'].items():
            shutil.copyfile(src,staging/name)
            if sha256(staging/name)!=files[name]['sha256']:raise NotDeliverable('export copy verification failed')
        write_json(staging/'delivery_receipt.json',record)
        if target.exists():
            history=destination/'history'/product;history.mkdir(parents=True,exist_ok=True);target.rename(history/(str(time.time_ns())))
        staging.rename(target)
    finally:
        if staging.exists():shutil.rmtree(staging)
    return record

def refresh(root=ROOT):
    root=Path(root);production=root/'output/production';folder=production/'user_repair30_20261002';destination=root/'exports/user_repair30_20261002';destination.mkdir(parents=True,exist_ok=True)
    with (destination/'.refresh.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        p=folder/'driver_status.json';state=json.loads(p.read_text(encoding="utf-8")) if p.exists() else {};q=Queue(production/'queue.sqlite');verified=[];withheld=[];seen=set()
        for entry in state.get('items',{}).values():
            if entry.get('state')!='completed':continue
            try:
                candidate=validate_product(q,entry)
                if candidate['product_id'] in seen:raise NotDeliverable('duplicate completed product')
                verified.append(copy_delivery(candidate,destination));seen.add(candidate['product_id'])
            except (ValueError,OSError,KeyError) as e:withheld.append({'product_id':entry.get('product_id'),'version_job_id':entry.get('new_job_id'),'reason':str(e)[:500]})
        index={'updated_at':time.time(),'count':len(verified),'target':30,'items':verified,'withheld':withheld,'scope':'Completed current user repairs only; no historical-ready fallback; not published'};write_json(destination/'index.json',index);write_json(folder/'delivery_index.json',index);return index


def audit_decision(record,expected):
    rows=record.get('review',{}).get('shots',[])
    if not record.get('finished_at') or record.get('status')=='audit_failed' or len(rows)!=len(expected) or {r.get('shot_id') for r in rows}!=set(expected):return None
    from workflow.audit_disposition import disposition
    triage={r['shot_id']:r['disposition'] for r in disposition(record)['shots']}
    # A detailed, media-bound review may resolve an initial minor finding.
    # Failed APIs, unbound reviews and unresolved uncertainty still block.
    return _audit_flags(rows, triage)

def audit_file_decision(path,expected):
    from workflow.sampled_supplement import resolved_shots
    record=json.loads(Path(path).read_text(encoding="utf-8"))
    flags=audit_decision(record,expected)
    if flags is None:return None
    resolved=resolved_shots(path)
    from workflow.audit_resolution import resolved_shots as adjudicated_shots
    resolved |= adjudicated_shots(path)
    return [sid for sid in flags if sid not in resolved]
