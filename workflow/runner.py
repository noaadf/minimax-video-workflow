"""Bounded worker execution, asset receipts and an SSH adapter for Ascend."""
import concurrent.futures
import json
import math
import os
import shlex
import shutil
import sqlite3
import subprocess
import threading
import traceback
import time
from pathlib import Path
from .code_identity import code_identity, workflow_code_sha256
from . import VERSION, media
from .core.io import digest, sha256, write_json
from .plan import freeze_assets, validate
from .queue import LostLease, Queue
from .core.failures import WorkflowFailure
from .review import editorial_review
from .review_state import mark_review_required
from .backends import ready as backend_ready, assignments, check_lease
RUNNER_CODE_SHA256 = code_identity(__file__)


class StageError(WorkflowFailure):
    def __init__(self, message, retryable=False, uncertain=False):
        code = ('submit_outcome_unknown' if uncertain else
                'runtime_not_adopted' if retryable else 'unclassified')
        super().__init__(code, message, effect='unknown' if uncertain else None)
        self.retryable = retryable
        self.uncertain = uncertain


def submit(queue, manifest_path, root, backend=None):
    path = Path(manifest_path).resolve()
    p = freeze_assets(validate(json.loads(path.read_text(encoding="utf-8"))), path.parent)
    from .storyboard import enabled, missing_previsuals
    story_mode = enabled(p)
    if story_mode:
        missing = missing_previsuals(p)
        if missing:
            raise ValueError('awaiting_storyboard: ' + ', '.join(missing))
    if p.get('id','').startswith('production_') and not p.get('parent_job_id'):
        policy_path=Path(__file__).parent/'config/production.json'
        p['novelty_policy']=json.loads(policy_path.read_text(encoding="utf-8")).get('novelty_policy',{'enabled':True})
    backend = backend or {'type':'assets'}
    if backend.get('type') == 'pool':
        backend = {**backend, 'scheduling': backend.get('scheduling', 'work_conserving')}
    if backend.get('type') not in ('assets','ascend_ssh','pool'):
        raise ValueError('unsupported generation backend')
    if any(not s.get('source_path') for s in p['shots']) and backend['type']=='assets':
        raise ValueError('assets backend requires source_path for every shot')
    if any(not s.get('source_path') for s in p['shots']) and not backend_ready(backend):
        raise ValueError('generation backend is not verified ready; preserve plan as awaiting_backend')
    if backend['type']=='ascend_ssh' and not backend.get('capability'):
        raise ValueError('SSH backend requires an exclusive capability/device group name')
    root = Path(root).resolve()
    # Fail closed at enqueue: a typo'd pipeline flag must not surface at first shot execution.
    from .film_ir_plan import (pipeline_of, PROMPT_CONTRACTS, compile_shot_from_plan,
                               _is_generated, _blocking, json_dumps)
    pipeline_of(p)
    if p.get('film_ir_prompt_contract', 'legacy_verbatim') not in PROMPT_CONTRACTS:
        raise ValueError(f'unknown film_ir_prompt_contract: {p.get("film_ir_prompt_contract")!r}')
    if pipeline_of(p) == 'film_ir':
        # Full compile pre-check: t2va shots, missing keyframes and infeasible slots
        # reject at submit instead of burning NPU time on a doomed generation —
        # and REUSED assets validate too (fit contract, window/retime feasibility),
        # so a repair-round plan fails here, not after its generated shots ran.
        from .film_ir_plan import validate_source_shot
        for s in p['shots']:
            if _is_generated(s):
                blocking = _blocking(compile_shot_from_plan(p, s))
                if blocking:
                    raise ValueError(f'film_ir compile violations block {s["id"]} at submit: '
                                     + json_dumps(blocking))
            elif s.get('source_path'):
                validate_source_shot(p, s)
    p['workflow_version'] = VERSION
    p['workflow_code_sha256'] = workflow_code_sha256()
    p['backend'] = backend
    allocated = assignments(backend, p['shots'])
    p['shot_backends'] = allocated
    run_digest = digest(p)
    folder = root / 'runs' / p['id'] / run_digest[:12]
    tasks = []
    def task(stage, name, payload, deps=(), capability='cpu'):
        tid = p['id'] + ':' + name
        tasks.append({'id':tid,'stage':stage,'capability':capability,
                      'payload':{'plan':p,'folder':str(folder),'data':payload},'deps':list(deps)})
        return tid
    voice = task('voice','voice', {})
    bed = task('music','music', {})
    preflight = task('story_preflight','story_preflight', {'voice':voice,'music':bed}, [voice,bed]) if story_mode else None
    shots = [task('shot', s['id'], s, deps=[preflight] if preflight else [],
                  capability='cpu' if s.get('source_path') else allocated[s['id']]['capability']) for s in p['shots']]
    composition = task('compose','compose', {'shots':shots,'voice':voice,'music':bed}, shots+[voice,bed])
    speech = task('speech','speech', {'compose':composition}, [composition])
    qc = task('quality','quality', {'compose':composition,'music':bed,'voice':voice,'speech':speech}, [composition,bed,voice,speech])
    task('export','export', {'compose':composition,'quality':qc}, [composition,qc])
    accepted = queue.submit(p['id'], run_digest, p, tasks)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder/'manifest.json', p)
    return {'job_id':p['id'],'new':accepted,'tasks':len(tasks),'run_dir':str(folder)}


def verify_inputs(plan):
    for path, expected in plan['input_sha256'].items():
        if not Path(path).is_file() or sha256(path) != expected:
            raise StageError('input asset changed after enqueue: ' + path)


def visual_generation_request(plan, shot, task, cfg):
    """Only explicitly authored visual instructions reach the video model.

    continuity_bible is a mixed editorial document, never a generation prefix.
    This function is pure so requests can be inspected without SSH or submission.
    With plan['shot_pipeline']=='film_ir' construction moves to film_ir_plan (single
    entry, compile violations raise — no fallback to the legacy dict below, which
    stays frozen for 'legacy' and 'film_ir_shadow' plans).
    """
    from .film_ir_plan import pipeline_of
    if pipeline_of(plan) == 'film_ir':
        from .film_ir_plan import compiled_request
        remote, request, manifest = compiled_request(plan, shot, task, cfg)
        return remote, request, manifest
    from .first_frame import validate_policy, enabled, require_profile
    validate_policy(plan)
    if enabled(plan):
        require_profile(cfg)
        if len(shot.get('reference_images', [])) != 1:
            raise ValueError('first_frame_only requires exactly one reviewed reference before dispatch')
    prompt = shot['prompt']
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('shot visual prompt must be nonempty text')
    visual = plan.get('visual_continuity', '')
    if not isinstance(visual, str):
        raise ValueError('visual_continuity must be explicitly authored text')
    if visual.strip():
        prompt = visual.strip() + '\n' + prompt
    if plan.get('pacing_policy')=='storyboard_v1' and max(4,math.ceil(shot['duration_seconds']))/shot['duration_seconds']>1.5:
        raise ValueError('Generated action cannot fit this slot naturally; retime storyboard before dispatch')
    remote = cfg['remote_root'].rstrip('/') + '/tasks/' + digest({
        'task': task['id'], 'shot': shot, 'visual_prompt': prompt,
        'prompt_contract': 'visual_only_v2'})[:24]
    return remote, {'schema_version':1,'task_id':task['id'].replace(':','-'),
                   'prompt':prompt,
                   'duration_seconds':max(4, math.ceil(shot['duration_seconds'])),
                   'aspect_ratio':'9:16','seed':shot.get('seed',1),
                   'output_dir':remote+'/output','reference_images':[],
                   'generation':cfg.get('generation',{'steps':50,'short_edge':768})}


def ssh_generate(plan, shot, task, folder):
    cfg = plan.get('shot_backends',{}).get(shot['id'], plan['backend'])
    if not backend_ready(cfg):raise StageError('generation backend is not verified ready')
    check_lease(cfg, require_expiry=True)
    host = cfg['host']
    if not host or host.startswith('-') or any(c.isspace() for c in host):
        raise ValueError('invalid SSH host')
    options = ['-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
               '-o','ConnectTimeout=20','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=3','-i',str(Path(cfg['identity_file']).expanduser())]
    port = str(int(cfg['port']))
    built = visual_generation_request(plan, shot, task, cfg)
    remote, request, film_ir_manifest = (*built, None) if len(built) == 2 else built
    def ssh(args):
        return media.run('ssh',*options,'-p',port,host,shlex.join([str(a) for a in args]),timeout=cfg.get('timeout_seconds',3600))
    def upload(local, dest):
        media.run('scp',*options,'-P',port,str(local),host+':'+dest,timeout=600)
    ssh(['mkdir','-p',remote])
    from .film_ir_plan import pipeline_of, shadow_entry, REFERENCE_SUFFIXES
    mode = pipeline_of(plan)
    if mode == 'film_ir':
        # The compiled request's local ref list is authoritative; same-path swaps that
        # survived compile would ride the old identity, so refs never come from the plan here.
        local_refs = request.pop('reference_images')
    else:
        local_refs = list(shot.get('reference_images', []))
    request['reference_images'] = []
    expected_shas = None
    if film_ir_manifest is not None:
        # Dispatch-time content binding with staging: copy each keyframe into the
        # attempt dir, hash the COPY, upload the COPY — the bytes that were hashed
        # are exactly the bytes that ship, closing the hash-then-reread TOCTOU gap.
        expected_shas = film_ir_manifest['shots'][0]['kf_shas']
    for i, ref in enumerate(local_refs):
        if mode == 'film_ir' and Path(ref).suffix.lower() not in REFERENCE_SUFFIXES:
            # New-input contract for the NEW pipeline only; legacy/shadow stay frozen.
            raise ValueError('unexpected reference image suffix: ' + ref)
        if expected_shas is not None:
            from .film_ir_plan import stage_reference
            ref = stage_reference(ref, expected_shas[i], folder/'refs', i)
        dest = remote+f'/reference_{i}'+Path(ref).suffix
        upload(ref,dest)
        request['reference_images'].append(dest)
    if mode == 'film_ir_shadow':
        # side-effect-free by contract: compile failures are recorded, never raised
        write_json(folder/'film_ir_shadow.json', shadow_entry(plan, shot, task, cfg, remote, request))
    if film_ir_manifest is not None:
        # Persist the dispatched segment so the settle stage can bind the artifact to
        # the segment that was ACTUALLY sent (same-path keyframe swaps re-key and must fail).
        write_json(folder/'film_ir_manifest.json', film_ir_manifest)
    write_json(folder/'request.json',request)
    upload(folder/'request.json',remote+'/request.json')
    # Worker persists server job ID. Re-entry reconciles the same generation instead of resubmitting.
    command = [cfg.get('python','python'),cfg['worker_script'],'--request',remote+'/request.json','--result',remote+'/result.json']
    remote_error = None
    try:
        ssh(command)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        remote_error = str(e)
    try:
        raw = ssh(['cat',remote+'/result.json'])
        result = json.loads(raw)
    except Exception as e:
        raise StageError('remote outcome unknown; reconcile task before retry: ' + (remote_error or str(e)), uncertain=True) from e
    write_json(folder/'worker_result.json',result)
    if result.get('task_id') != request['task_id']:
        raise StageError('worker returned a different task id')
    from .first_frame import enabled, silent_failure, remote_evidence_script, verify_and_strip
    if enabled(plan) and (result.get('status') == 'succeeded' or silent_failure(result)):
        # Reconcile existing bytes only. The failed worker receipt stays intact;
        # no POST, retry, server flag or generic audio threshold is changed.
        try:
            evidence=json.loads(ssh([cfg.get('python','python'),'-c',remote_evidence_script(
                request,cfg,failed=result.get('status') != 'succeeded')]))
            write_json(folder/'visual_only_remote_evidence.json',evidence)
            output=evidence['raw_path']
            if not output.startswith(remote+'/output/') or '..' in Path(output).parts:
                raise ValueError('visual_only raw escaped its output directory')
            media.run('scp',*options,'-P',port,host+':'+output,folder/'native_raw.mp4',timeout=600)
            receipt=verify_and_strip(folder/'native_raw.mp4',folder/'generated.mp4',request,result,evidence,cfg,
                                     expected_reference_sha256=[sha256(p) for p in shot['reference_images']])
            return folder/'generated.mp4',{'visual_only':receipt,'audio_preserved':False,'native_audio_consumed':False}
        except Exception as exc:
            raise StageError('visual_only reconciliation refused: '+str(exc)) from exc
    if result.get('status') != 'succeeded':
        err = result.get('error',{})
        uncertain = err.get('code') in ('SUBMISSION_AMBIGUOUS','POLL_TIMEOUT','TASK_RUNNING')
        raise StageError(err.get('message','worker failed'),err.get('retryable',False) and not uncertain, uncertain=uncertain)
    output = result['video_path']
    if not output.startswith(remote+'/output/') or '..' in Path(output).parts:
        raise StageError('worker result escaped its output directory')
    media.run('scp',*options,'-P',port,host+':'+output,folder/'generated.mp4',timeout=600)
    return folder/'generated.mp4',result.get('metrics',{})


def _normalize_executed_shot(p, data, src, metrics, folder):
    from .film_ir_plan import pipeline_of
    if pipeline_of(p)=='film_ir':
        # Integer-slot contract for the WHOLE timeline: generated shots settle
        # against measured frames; reused assets render exact slot windows too
        # (mixed rounding left whole films a frame short — astra 2340 r1: 1102 vs 1103).
        if data.get('source_path'):
            from .film_ir_plan import render_source_shot
            info = render_source_shot(p, data, src, folder/'shot.mp4')
            source_seconds = info['source_seconds']
        else:
            from .film_ir_plan import render_compiled_shot
            persisted = folder/'film_ir_manifest.json'
            if not persisted.is_file():
                # ssh_generate in film_ir mode ALWAYS writes it; a missing file means
                # the attempt folder is not the one that dispatched — fail closed.
                raise StageError('film_ir manifest missing for generated shot; '
                                 'refusing to settle without the dispatched segment binding')
            info = render_compiled_shot(p, data, src, folder/'shot.mp4',
                                        submitted=json.loads(persisted.read_text(encoding="utf-8")))
            source_seconds = info['native_seconds']
        write_json(folder/'film_ir_settle.json',info)
        result = {'video_path':str(folder/'shot.mp4'),'metrics':metrics,'film_ir':info}
        # 'whole_action' keeps the timing-repair classifier working (it keys on
        # this exact value); the pipeline marker rides in a separate field.
        result['picture_timing']={'fit_policy':'whole_action',
            'pipeline':'film_ir_edit_plan',
            'source_seconds':source_seconds,'slot_seconds':data['duration_seconds']}
    else:
        fit=data.get('source_fit_policy','source_window') if data.get('source_path') else 'whole_action' if p.get('pacing_policy')=='storyboard_v1' else 'source_window'
        media.normalize_shot(src,folder/'shot.mp4',data['duration_seconds'],data.get('source_start',0),fit_policy=fit,framing_policy=p.get('framing_policy'))
        result = {'video_path':str(folder/'shot.mp4'),'metrics':metrics}
        result['picture_timing']={'fit_policy':fit,'source_seconds':media.duration(src),'slot_seconds':data['duration_seconds']}
    return result


def _attach_native_foley(p, data, src, result, folder):
    native_foley = p.get('use_native_foley',False) and data.get('use_native_foley',True)
    if native_foley and data.get('foley_source_path'):
        shutil.copyfile(data['foley_source_path'],folder/'foley.wav');result['foley_path']=str(folder/'foley.wav')
    elif native_foley and any(x['codec_type']=='audio' for x in media.probe(src)['streams']):
        media.ff('-ss',data.get('source_start',0),'-i',src,'-vn','-t',data['duration_seconds'],
                 '-af',f"apad=whole_dur={data['duration_seconds']},atrim=duration={data['duration_seconds']}",
                 '-ar','48000','-ac','2',folder/'foley.wav')
        result['foley_path']=str(folder/'foley.wav')


def _retain_quality_speech(p, data, deps, result, folder):
    from .speech_gate import screen as screen_speech, retain_review as retain_speech_review
    if data.get('speech'):
        result['speech'] = deps[data['speech']]
    else:
        # Compatibility with already queued graphs; do not resubmit old films.
        result['speech'] = screen_speech(deps[data['compose']]['video_path'], p, folder/'speech') if result['passed'] else {
            'status':'speech_not_assessed', 'reason':'Technical QC failed; ASR skipped',
            'review_required':True, 'content_approved':False}
    result['editorial'] = retain_speech_review(result['editorial'], result['speech'])


def _execute_shot(p, data, deps, task, folder):
    from .storyboard import enabled
    if enabled(p):
        evidence = deps.get(p['id'] + ':story_preflight', {})
        if evidence.get('status') != 'ready_for_generation' or not evidence.get('sha256'):
            raise StageError('story_preflight must succeed before shot execution')
    if data.get('source_path'):
        src, metrics = data['source_path'],{'reused_asset':True}
    else:
        src, metrics = ssh_generate(p,data,task,folder)
    result = _normalize_executed_shot(p, data, src, metrics, folder)
    _attach_native_foley(p, data, src, result, folder)
    return result


def _execute_voice(p, folder):
    cached=p.get('prebuilt_narration')
    if cached:
        seconds = media.duration(cached['path'])
        if not math.isfinite(seconds) or seconds <= 0 or abs(seconds-p['duration_seconds']) > .1:
            raise StageError('cached narration has wrong duration')
        shutil.copyfile(cached['path'],folder/'narration.wav')
        result={'audio_path':str(folder/'narration.wav'),'effective_narration':cached['effective_narration'],'segments':cached['segments'],'reused_audio':True}
    else:
        result = {'audio_path':str(folder/'narration.wav'),**media.narration(p,folder/'narration.wav')}
    return result


def _execute_music(p, folder):
    cached = p.get('prebuilt_music')
    if cached:
        seconds = media.duration(cached['path'])
        if not math.isfinite(seconds) or seconds <= 0 or abs(seconds-p['duration_seconds']) > .1:
            raise StageError('cached music has wrong duration')
        shutil.copyfile(cached['path'],folder/'music.wav')
    else:
        media.music(p,folder/'music.wav')
    result = {'audio_path':str(folder/'music.wav')}
    if cached:
        result['reused_audio'] = True
    return result


def _execute_story_preflight(p, deps, data, folder):
    from .storyboard import preflight
    result = preflight(p, deps[data['voice']], deps[data['music']], folder)
    return result


def _execute_compose(p, data, deps, folder):
    p = {**p,'narration':deps[data['voice']]['effective_narration']}
    p = media.clean_audio_plan(p)
    write_json(folder/'effective_manifest.json',p)
    result = media.compose(p,[deps[x]['video_path'] for x in data['shots']],
                           deps[data['voice']]['audio_path'],deps[data['music']]['audio_path'],folder)
    result['manifest_path'] = str(folder/'effective_manifest.json')
    return result


def _execute_speech(data, deps, folder):
    from .speech_gate import screen as screen_speech
    p = json.loads(Path(deps[data['compose']]['manifest_path']).read_text(encoding="utf-8"))
    result = screen_speech(deps[data['compose']]['video_path'], p, folder)
    if result['status'] == 'speech_deferred':
        raise StageError(result['reason'], retryable=True)
    result['report_path'] = str(folder/'report.json')
    return result


def _assess_closing_audio(p, data, deps, result):
    if data.get('voice'):
        from .closing_audio import evaluate, pcm
        result['closing_audio'] = evaluate(pcm(deps[data['compose']]['video_path']),
            pcm(deps[data['voice']]['audio_path']), pcm(deps[data['music']]['audio_path']),
            p, deps[data['voice']].get('segments', []))
        result['passed'] = result['passed'] and result['closing_audio']['programmatic_passed']
    else:
        result['closing_audio'] = {'status':'not_assessed', 'reason':'Legacy queued task lacks voice dependency'}


def _execute_quality(data, deps, folder):
    p = json.loads(Path(deps[data['compose']]['manifest_path']).read_text(encoding="utf-8"))
    result = media.quality(p,deps[data['compose']]['video_path'],deps[data['music']]['audio_path'])
    _assess_closing_audio(p, data, deps, result)
    from .programmatic_qc import screen_video
    result['programmatic'] = screen_video(deps[data['compose']]['video_path'], folder/'programmatic', p)
    from .temporal_qc import scan, retain_exceptions
    result['temporal'] = scan(deps[data['compose']]['video_path'], folder/'temporal', p,
        intentional_cuts=p.get('intentional_cut_seconds', []),
        voice_path=deps[data['voice']]['audio_path'] if data.get('voice') else None)
    result['passed'] = result['passed'] and result['temporal']['technical_scan_passed']
    if result['passed'] and not result['programmatic']['hard_failed']:
        result['editorial'] = editorial_review(p,deps[data['compose']]['contact_path'])
    else:
        result['editorial'] = {'status':'review_required','reason':'Programmatic QC failed; API skipped'}
        result['passed'] = False
    result['editorial'] = retain_exceptions(result['editorial'], result['temporal'], p)
    from .shot_review import review_film
    result['shot_review'] = review_film(p, deps[data['compose']]['video_path'], folder/'shot_reviews',
                                      editorial=result['editorial'], screening={**result['programmatic'], 'hard_failed':not result['passed']})
    result['storyboard_required'] = p.get('pacing_policy') == 'storyboard_v1'
    if result['shot_review']['status'] not in ('sampled_checks_passed','no_exception_review_needed'):
        review = mark_review_required(result['editorial'])
        for shot_result in result['shot_review'].get('shots', []):
            for finding in shot_result.get('review', {}).get('findings', []):
                review.setdefault('findings', []).append({**finding, 'shot_ids':[shot_result['shot_id']]})
    _retain_quality_speech(p, data, deps, result, folder)
    result['editorial_status'] = result['editorial']['status']
    from .delivery_policy import resolve
    result['delivery_policy']=resolve(result,p)
    write_json(folder/'quality.json',result)
    result['report_path'] = str(folder/'quality.json')
    if not result['passed']:
        raise StageError('technical QC failed: '+json.dumps({'media':result['checks'],
            'closing_audio':result['closing_audio'].get('checks'),
            'programmatic':result['programmatic']['checks']}))
    return result


def _execute_export(data, deps, folder):
    if not deps[data['quality']]['passed']:
        raise StageError('refusing export after failed quality check')
    src = deps[data['compose']]
    p = json.loads(Path(src['manifest_path']).read_text(encoding="utf-8"))
    # Export is review-ready; publishing is a separate future operation.
    for key, filename in [('video_path','video.mp4'),('cover_path','cover.png'),('contact_path','contact.jpg')]:
        shutil.copyfile(src[key],folder/filename)
    write_json(folder/'quality.json',deps[data['quality']])
    from .profiles import publication_text
    (folder/'publication.txt').write_text(publication_text(p), encoding="utf-8")
    write_json(folder/'manifest.json',p)
    if p.get('engagement'):
        write_json(folder/'engagement.json',p['engagement'])
        (folder/'pinned_comment_draft.txt').write_text(p['comment_prompt'], encoding="utf-8")
    result = {'export_dir':str(folder),'video_path':str(folder/'video.mp4'),'cover_path':str(folder/'cover.png'),
              'publication_path':str(folder/'publication.txt'),'state':deps[data['quality']]['editorial_status']}
    return result


def execute(queue, task):
    payload = task['payload']
    p = payload['plan']
    if payload.get('backend_override'):
        cfg=payload['backend_override']
        if task['capability']!=cfg.get('capability') or not backend_ready(cfg):
            raise StageError('dispatch override does not match verified worker capability')
        p={**p,'shot_backends':{**p.get('shot_backends',{}),payload['data']['id']:cfg}}
    verify_inputs(p)
    # A stale lease can finish writing its own attempt directory but cannot publish or overwrite a winner.
    folder = Path(payload['folder'])/'attempts'/task['id'].split(':',1)[1]/task['token']
    folder.mkdir(parents=True,exist_ok=True)
    data = payload['data']
    stage = task['stage']
    deps = queue.results(task['deps'])
    for result in deps.values():
        for path, expected in result.get('sha256',{}).items():
            if not Path(path).is_file() or sha256(path) != expected:
                raise StageError('dependency artifact is missing or changed: ' + path)
    started = time.monotonic()
    if stage=='shot':
        result = _execute_shot(p, data, deps, task, folder)
    elif stage=='voice':
        result = _execute_voice(p, folder)
    elif stage=='music':
        result = _execute_music(p, folder)
    elif stage=='story_preflight':
        result = _execute_story_preflight(p, deps, data, folder)
    elif stage=='compose':
        result = _execute_compose(p, data, deps, folder)
    elif stage=='speech':
        result = _execute_speech(data, deps, folder)
    elif stage=='quality':
        result = _execute_quality(data, deps, folder)
    elif stage=='export':
        result = _execute_export(data, deps, folder)
    else:
        raise StageError('unknown stage: '+stage)
    result['elapsed_seconds'] = round(time.monotonic()-started,3)
    if stage=='shot' and not data.get('source_path'):
        cfg=p.get('shot_backends',{}).get(data['id'],p['backend'])
        result['execution_backend']={'capability':cfg['capability'],'resource_id':cfg.get('resource_id'),'port':cfg['port']}
    result['sha256'] = {v:sha256(v) for k,v in result.items() if k.endswith('_path') and isinstance(v,str) and Path(v).is_file()}
    if stage == 'speech' and 'receipt_path' in result:
        # A later fingerprint must not replace the bytes accepted by speech screening.
        if not result.get('receipt_sha256') or result['sha256'].get(result['receipt_path']) != result['receipt_sha256']:
            raise StageError('speech receipt is missing or changed after screening; diagnose before retry')
    if stage=='voice':
        for line in result.get('effective_narration', []):
            for key in ('audio_path','word_timing_path'):
                if line.get(key):
                    result['sha256'][line[key]] = sha256(line[key])
    write_json(folder/'receipt.json',result)
    return result


def _has_matching_work(status, capabilities, stages):
    return any(((t['capability'] in capabilities or ('cpu' not in capabilities and t['stage'] == 'shot' and (t['status'] == 'pending'))) and (stages is None or t['stage'] in stages) and (t['status'] in ('pending', 'running')) for t in status))


def _requires_unconfigured_worker(status, capabilities, configured_capabilities, stages=None):
    # Don't spin forever when a dependency requires an unconfigured worker.
    # A lane only judges the stages it serves: downstream CPU tasks of the same
    # job belong to the CPU lanes, not to this accelerator lane.
    remaining = [t for t in status if t['status'] in ('pending','running') and (stages is None or t['stage'] in stages)]
    available = configured_capabilities if configured_capabilities is not None else capabilities
    if any(t['capability'] not in available for t in remaining) and not any(t['status']=='running' for t in remaining):
        return True
    return False


def _worker_can_progress(status, capabilities, stages, configured_capabilities):
    if any(t['status']=='reconcile' for t in status) and not any(t['status']=='running' for t in status):
        return False
    if not any(t['status'] in ('pending','running') for t in status):
        return False
    if not _has_matching_work(status, capabilities, stages):
        return False
    if _requires_unconfigured_worker(status, capabilities, configured_capabilities, stages):
        return False
    return True


def work(queue, capabilities, stop, job_ids=None, stages=None, configured_capabilities=None):
    while not stop.is_set():
        try:
            task = queue.claim(capabilities, job_ids=job_ids, stages=stages)
        except sqlite3.OperationalError as exc:
            # A failed claim rolls back before external work or a lease exists.
            # Keep the lane alive after bounded DB lock acquisition is exhausted;
            # result-commit errors below still require reconciliation, not replay.
            code = getattr(exc, 'sqlite_errorcode', None)
            if code is None or (code & 0xff) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            stop.wait(.5)
            continue
        if not task:
            status = [t for t in queue.status() if job_ids is None or t['job_id'] in job_ids]
            if not _worker_can_progress(status, capabilities, stages, configured_capabilities):
                return
            stop.wait(.5)
            continue
        heartbeat_stop = threading.Event()
        lost = threading.Event()
        def heartbeat():
            while not heartbeat_stop.wait(10):
                try:
                    queue.heartbeat(task['id'],task['token'])
                except LostLease:
                    lost.set()
                    return
        thread = threading.Thread(target=heartbeat,daemon=True)
        thread.start()
        phase = "execute"
        result = None
        try:
            result = execute(queue,task)
            if lost.is_set():
                raise LostLease(task['id'])
            phase = 'commit_result'
            queue.finish(task,result=result)
            print(json.dumps({'task':task['id'],'status':'succeeded','seconds':result['elapsed_seconds']},ensure_ascii=False),flush=True)
        except LostLease:
            print(json.dumps({'task':task['id'],'status':'lost_lease'}),flush=True)
        except Exception as e:
            try:
                queue.finish(task,result=result if phase=='commit_result' else None,
                             error=f'{phase}: {e}',
                             retryable=phase=='execute' and isinstance(e,StageError) and e.retryable,
                             uncertain=phase=='commit_result' or (isinstance(e,StageError) and e.uncertain))
            except LostLease:
                pass
            print(json.dumps({'task':task['id'],'status':'error','error':str(e),'phase':phase,'traceback':traceback.format_exc()},ensure_ascii=False),flush=True)
        finally:
            heartbeat_stop.set()
            thread.join()


def drain(queue, capabilities, workers, job_ids=None):
    stop = threading.Event()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work,queue,capabilities,stop,job_ids) for _ in range(workers)]
            for f in futures:
                f.result()
    except KeyboardInterrupt:
        stop.set()
        raise


def drain_partitioned(queue, capabilities, media_workers=1, review_workers=1, job_ids=None):
    """Keep API review from blocking media/export, sharing one durable queue.

    Named accelerator groups each get one worker; Queue still enforces device
    exclusivity across processes. No additional accelerator jobs are submitted.
    """
    if not 1 <= media_workers <= 8 or not 1 <= review_workers <= 3:
        raise ValueError('media_workers must be 1..8; review_workers must be 1..3')
    caps = set(capabilities)
    media_stages = {t['stage'] for t in queue.status() if t['stage'] not in {'quality', 'speech'}}
    lanes = [([cap], None) for cap in sorted(caps - {'cpu'})]
    if 'cpu' in caps:
        lanes += [(['cpu'], media_stages)] * media_workers
        lanes += [(['cpu'], {'quality'})] * review_workers
        lanes += [(['cpu'], {'speech'})]
    if not lanes:
        return
    stop = threading.Event()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(lanes)) as pool:
        futures = [pool.submit(work, queue, lane, stop, job_ids, stages, caps)
                   for lane, stages in lanes]
        try:
            for future in concurrent.futures.as_completed(futures):
                future.result()
        finally:
            stop.set()
