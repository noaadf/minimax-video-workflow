"""Admit a reviewed storyboard through the existing singleton production owner."""
import fcntl
import json
import os
import time
from pathlib import Path
from .core.io import sha256, write_json
from .core.failures import Disposition, WorkflowFailure, classify

MAX_HELD_TICKS = 3
_ADMISSION_STATES = {Disposition.RETRY: 'held', Disposition.TERMINAL: 'rejected',
                     Disposition.UNCERTAIN: 'isolated'}


class PostSubmitUncertainty(WorkflowFailure):
    """submit() failed after an unknown commit point — never retried."""
    def __init__(self, message):
        super().__init__('submit_outcome_unknown', message, effect='unknown')


def admission_disposition(exc):
    """Only explicit transient failures are held; uncertainty stays isolated."""
    return _ADMISSION_STATES[classify(exc).disposition]


def _require_renderer_runtime(root,request):
    from . import media
    if request.get('required_media_version') and request['required_media_version']!=getattr(media,'NORMALIZATION_VERSION',None):
        raise WorkflowFailure('runtime_not_adopted', 'Required whole-action normalizer is not loaded; drain and update existing owner')
    if request.get('required_story_review_version'):
        # The renderer runs independently; inspect its actual startup receipt,
        # rather than importing newer files in this controller and assuming adoption.
        receipt_path=Path(request['renderer_runtime_path']).resolve()
        if not receipt_path.is_relative_to(root):raise WorkflowFailure('contract_violation', 'Renderer receipt outside production root')
        receipt=json.loads(receipt_path.read_text(encoding="utf-8"))
        if request.get('required_media_sha256') and receipt.get('loaded_media_sha256')!=request['required_media_sha256']:
            raise WorkflowFailure('runtime_not_adopted', 'Required color/media implementation is not loaded by renderer')
        if receipt.get('story_review_version')!=request['required_story_review_version']:
            raise WorkflowFailure('runtime_not_adopted', 'Required story reviewer is not loaded by the existing renderer')
        if request.get('required_story_preflight_version') and receipt.get('story_preflight_version')!=request['required_story_preflight_version']:
            raise WorkflowFailure('runtime_not_adopted', 'Required preflight verifier is not loaded by the existing renderer')
        os.kill(int(receipt['pid']),0)


def _admission_evidence(root,request):
    manifest=Path(request['manifest_path']).resolve()
    review=Path(request['review_path']).resolve()
    preflight=Path(request['preflight_path']).resolve()
    if any(not p.is_relative_to(root/'storyboard_rollout') for p in (manifest,review,preflight)):
        raise ValueError('Rollout evidence must remain in rollout folder')
    for p,key in ((manifest,'manifest_sha256'),(review,'review_sha256'),(preflight,'preflight_sha256')):
        if sha256(p)!=request.get(key):raise ValueError('Rollout evidence changed: '+p.name)
    from .narrative_gate import require_review
    from .storyboard import enabled,missing_previsuals
    require_review(manifest,review)
    plan=json.loads(manifest.read_text(encoding="utf-8"))
    if not enabled(plan) or missing_previsuals(plan):raise ValueError('Complete storyboard required')
    from .first_frame import enabled as first_frame_enabled, require_runtime
    if first_frame_enabled(plan):
        receipt=json.loads(Path(request['renderer_runtime_path']).read_text(encoding="utf-8"))
        os.kill(int(receipt['pid']),0)
        try:
            require_runtime(receipt,request.get('required_first_frame_runtime'))
        except ValueError as exc:
            raise WorkflowFailure('runtime_not_adopted', str(exc)) from exc
    return manifest,plan,preflight


def _require_preflight(root,plan,preflight):
    acceptance=json.loads(preflight.read_text(encoding="utf-8"))
    if acceptance.get('status')!='ready_for_generation':raise ValueError('Actual preflight must pass')
    for key in ('manifest','visual_review','edl','preview'):
        evidence=Path(acceptance[key+'_path']).resolve()
        if not evidence.is_relative_to(root/'storyboard_rollout') or sha256(evidence)!=acceptance.get(key+'_sha256'):
            raise ValueError('Preflight evidence changed: '+key)
    visual=json.loads(Path(acceptance['visual_review_path']).read_text(encoding="utf-8"))
    if visual.get('status')!='automatic_review_passed' or not visual.get('receipt',{}).get('model'):
        raise ValueError('Completed visual model review required')
    from .narrative_gate import authored_fields
    preflight_plan=json.loads(Path(acceptance['manifest_path']).read_text(encoding="utf-8"))
    if any(plan.get(k)!=preflight_plan.get(k) for k in authored_fields(plan)):
        raise ValueError('Authored story changed after visual preflight')
    # Validate every frozen animatic source, not just an editable pass flag.
    from .roughcut import validate
    edl=Path(acceptance['edl_path']).resolve()
    if not edl.is_relative_to(root/'storyboard_rollout'):raise ValueError('Preflight EDL outside rollout')
    validate(json.loads(edl.read_text(encoding="utf-8")))


def _submit_reviewed(queue,root,manifest,plan,request,request_path,status,status_path):
    with queue.connect() as c:
        row=c.execute('SELECT manifest FROM jobs WHERE id=?',(plan['parent_job_id'],)).fetchone()
        current=c.execute('SELECT current_job FROM production_items WHERE item_id=?',(plan['root_job_id'],)).fetchone()
    if not row or not current or current['current_job']!=plan['parent_job_id']:
        raise ValueError('Parent product changed before storyboard admission')
    from workflow.revised_sources import validate_revised_sources
    validate_revised_sources(plan,json.loads(row['manifest']))
    from .runner import submit
    backend=json.loads(Path(request['backend_file']).read_text(encoding="utf-8"))
    try:
        result=submit(queue,manifest,root,backend)
    except Exception as submit_exc:
        # Post-submit uncertainty: the queue commit may or may not have
        # landed. Retry would double-submit; this stays isolated forever.
        raise PostSubmitUncertainty(str(submit_exc)) from submit_exc
    request.update(state='rendering',job_id=result['job_id'],run_dir=result['run_dir'],admitted_at=time.time())
    write_json(request_path,request)
    status.update(state='preflight_running',runtime_adoption='active_singleton',job_id=result['job_id'],
                  next_step='生产入口核对冻结素材与粗剪证据，通过后生成动作镜头')
    write_json(status_path,status)


def _pending_render(tasks,job,status,status_path):
    preflight_done=any(t['stage']=='story_preflight' and t['status']=='succeeded' for t in tasks)
    shots=[t for t in tasks if t['stage']=='shot']
    phase='composing' if shots and all(t['status']=='succeeded' for t in shots) else 'generating' if preflight_done else 'preflight_running'
    if status.get('state')!=phase:
        status.update(state=phase,next_step={'composing':'合成新镜头并检查声音、字幕和完整成片',
            'generating':'生成新的动作镜头；之后检查每镜的动作结果和状态连续性',
            'preflight_running':'生产入口核对冻结素材与粗剪证据，通过后生成动作镜头'}[phase])
        write_json(status_path,status)
    return [job]


def _defer_quality(phase,request,request_path,status,status_path):
    request.update(state='isolated' if phase=='failed' else 'awaiting_quality_review',
                   reason='story_or_delivery_gate_failed' if phase=='failed' else 'required_quality_review_pending')
    write_json(request_path,request)
    status.update(state='film_needs_revision' if phase=='failed' else 'film_checks_pending',film_ready=False,
        next_step='按实证缺陷修订' if phase=='failed' else '保留成片与原检查回执，等待缺失或失败的审核完成；不重新生成')
    write_json(status_path,status);return []


def _quality_result(queue,folder,request,request_path,status,status_path,job,tasks):
    results={t['stage']:json.loads(t['result']) for t in tasks if t['stage'] in ('quality','export')}
    quality=results['quality']
    plan=json.loads(Path(request['manifest_path']).read_text(encoding="utf-8"))
    from .delivery_policy import resolve,gate_state
    adjudication=folder/'supervisor_adjudication.json'
    if adjudication.is_file():
        # Uncertainty holds the static sampler cannot decide (motion, confidence)
        # resolve only through a bound supervisor record; defects never clear here.
        from .supervisor_adjudication import validate as adjudicate, apply_to as adjudicated_view
        cleared,editorial=adjudicate(adjudication,Path(quality['report_path']),Path(results['export']['video_path']))
        quality=adjudicated_view(quality,cleared,editorial)
    policy=resolve(quality,plan)
    # The producer historically omitted policy for strict profiles. Derive the
    # same profile's gate from immutable evidence, never trust a missing flag or
    # overwrite the original quality/export result to manufacture acceptance.
    if quality.get('storyboard_required') is not True:
        policy['ready_for_delivery']=False
        policy['blockers'].append({'category':'story_action_unverified','issue':'Required storyboard evidence missing'})
    phase=gate_state(quality,policy)
    from .core.io import digest
    write_json(folder/'quality_gate_diagnostic.json',{'quality_digest':digest(quality),
        'manifest_sha256':sha256(request['manifest_path']),'policy':policy,'state':phase,
        'original_delivery_policy_present':'delivery_policy' in quality,'recorded_at':time.time()})
    if phase!='ready':
        return _defer_quality(phase,request,request_path,status,status_path)
    from .queue import begin_immediate
    with queue.connect() as c:
        begin_immediate(c)
        current=c.execute('SELECT current_job FROM production_items WHERE item_id=?',(plan['root_job_id'],)).fetchone()
        if current and current['current_job'] in (plan['parent_job_id'],job):
            c.execute('UPDATE production_items SET current_job=?,state=?,detail=?,updated=? WHERE item_id=?',
                (job,'ready_for_review',json.dumps({'action':'storyboard_rollout','previous_job':plan['parent_job_id'],
                'review_scope':'automatic technical and sampled story checks; human review pending'}),time.time(),plan['root_job_id']))
        else:raise ValueError('Product advanced during rollout; preserve the newer product version')
    import shutil
    source=Path(results['export']['video_path']);film=folder/'film'/f'{job}.mp4'
    film.parent.mkdir(parents=True,exist_ok=True)
    temp=film.with_suffix('.tmp');shutil.copyfile(source,temp);temp.replace(film)
    request.update(state='completed',finished_at=time.time(),film_path=str(film),film_sha256=sha256(film))
    status.update(state='film_review_required',film_ready=True,film_path=str(film),
        next_step='新版已通过自动检查，请完整观看确认；继续依据实际反馈改进')
    write_json(request_path,request);write_json(status_path,status);return []


def _rendering_result(queue,folder,request,request_path,status,status_path):
    job=request['job_id'];tasks=[t for t in queue.status() if t['job_id']==job]
    failed=[t for t in tasks if t['status'] in ('failed','blocked','reconcile')]
    if failed:
        request.update(state='isolated',failures=[{'id':t['id'],'stage':t['stage'],'error':t.get('error')} for t in failed])
        status.update(state='preflight_needs_revision',film_ready=False,
            next_step='新动作镜头或质量检查失败，保留原成片并按失败证据修订')
        write_json(request_path,request);write_json(status_path,status);return []
    if not tasks or any(t['status']!='succeeded' for t in tasks):
        return _pending_render(tasks,job,status,status_path)
    return _quality_result(queue,folder,request,request_path,status,status_path,job,tasks)


def _tick_one(queue,root,owner_lock_fd,folder):
    root=Path(root).resolve();folder=Path(folder).resolve()
    if not folder.is_relative_to(root/'storyboard_rollout'):raise ValueError('Request folder outside rollout')
    request_path=folder/'deployment_request.json'
    if not request_path.exists():return []
    lock=Path(queue.path+'.controller-service.lock')
    if owner_lock_fd is None or os.fstat(owner_lock_fd).st_ino!=lock.stat().st_ino:
        raise ValueError('Existing singleton owner lock required')
    fcntl.flock(owner_lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    request=json.loads(request_path.read_text(encoding="utf-8"))
    if request.get('state') in ('completed','isolated','rejected'):return []
    status_path=folder/'status.json';status=json.loads(status_path.read_text(encoding="utf-8"))
    if request.get('state') in ('approved','held'):
        _require_renderer_runtime(root,request)
        from .production_control import paused
        if paused(root):return []
        manifest,plan,preflight=_admission_evidence(root,request)
        _require_preflight(root,plan,preflight)
        _submit_reviewed(queue,root,manifest,plan,request,request_path,status,status_path)
    if request.get('state') not in ('rendering','awaiting_quality_review'):return []
    return _rendering_result(queue,folder,request,request_path,status,status_path)


def tick(queue,root,owner_lock_fd):
    """Keep legacy admission; isolate each versioned batch request independently."""
    root=Path(root).resolve(); rollout=root/'storyboard_rollout'
    requests=sorted((rollout/'requests').glob('*/deployment_request.json'))
    if not requests:
        return _tick_one(queue,root,owner_lock_fd,rollout)
    lock=Path(queue.path+'.controller-service.lock')
    if owner_lock_fd is None or os.fstat(owner_lock_fd).st_ino!=lock.stat().st_ino:
        raise ValueError('Existing singleton owner lock required')
    fcntl.flock(owner_lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    jobs=[]
    # Legacy failure must not starve independent batch products.
    for folder in [rollout]+[p.parent for p in requests]:
        try:
            jobs.extend(_tick_one(queue,root,owner_lock_fd,folder))
        except Exception as exc:
            request_path=folder/'deployment_request.json'
            try:
                request=json.loads(request_path.read_text(encoding="utf-8"))
            except (ValueError,OSError):
                write_json(folder/'admission_error.json',{'error':str(exc),'state':'invalid_request'})
                continue
            # Provider/runtime failures are product-local too. Keep uncertain
            # submissions isolated; never reset them to approved on a later tick.
            # BaseException (process death/interrupt) deliberately propagates.
            failure=classify(exc)
            request.update(failure=failure.record(), classification_source='exception')
            disposition=admission_disposition(exc)
            if disposition=='held' and int(request.get('held_ticks',0))+1<=MAX_HELD_TICKS:
                request.update(state='held',held_ticks=int(request.get('held_ticks',0))+1,
                               reason='transient_admission',error=str(exc))
            else:
                request.update(state='isolated' if disposition=='held' else disposition,
                               reason='admission_or_reconciliation_failed',error=str(exc))
            write_json(request_path,request)
            write_json(folder/'admission_error.json',{'error':str(exc),'state':request['state'],'failure':failure.record(),'classification_source':'exception'})
    return list(dict.fromkeys(jobs))
