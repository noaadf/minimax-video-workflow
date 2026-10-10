"""Explicit user rework, one version per request, sole queue owner; no publication."""
import argparse,copy,importlib.util,json,os,sqlite3,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
# fcntl must come after ROOT joins sys.path: stdlib on POSIX, ROOT/fcntl.py shim on Windows.
import fcntl
from workflow.plan import write_json,sha256,digest,validate
from workflow.queue import Queue,begin_immediate
from workflow.production_control import paused
from workflow.repair import job_bundle
from workflow.runner import submit
OUT=ROOT/'output/production/user_repair30_20261002'
# venv layout differs per platform; pick the interpreter that actually exists.
_VENVPY=ROOT/'.venv-workflow'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
PYTHON=str(_VENVPY if _VENVPY.exists() else ROOT/'.venv-workflow/bin/python')
AUDITOR=ROOT/'research/user_repair30_20261002/visual_audit.py'
OVERRIDES={}
from workflow.repair_delivery import audit_decision, audit_file_decision

def narrative_overrides():
    path=OUT/'narrative_approvals.json'
    if not path.exists():
        narrative_overrides.last_errors={}
        return {}
    document=json.loads(path.read_text(encoding="utf-8"));entries=document.get('items',[])
    if document.get('status') not in ('partial','approved') or len(entries)>30 or len({x.get('product_id') for x in entries})!=len(entries):raise ValueError('Invalid per-product narrative approval index')
    result={};narrative_overrides.last_errors={}
    for x in entries:
        if x.get('status')!='approved':continue
        try:
            if not all(x.get('checks',{}).get(k) is True for k in ('single_core_question','causal_progression','shot_narration_alignment','continuity')):raise ValueError('Narrative review incomplete')
            if sha256(x['manifest_path'])!=x['sha256'] or sha256(x['review_evidence_path'])!=x['review_evidence_sha256']:raise ValueError('Narrative evidence changed')
            from workflow.narrative_gate import require_review
            require_review(x['manifest_path'],x['review_evidence_path'])
            result[x['product_id']]={**x,'reviewed':True}
        except (ValueError,OSError,KeyError) as exc:
            narrative_overrides.last_errors[x.get('product_id','unknown')]=str(exc)
    return result




def validate_revised_sources(plan,parent):
    """Preserve product identity while verifying original/supplemental archives."""
    from workflow.revised_sources import validate_revised_sources as validate
    return validate(plan,parent)


def verified_narration_assets(plan):
    """Reuse only supplied clips bound to this exact line and voice, never parent audio."""
    evidence = []
    for line in plan['narration']:
        if not line.get('audio_path'):
            continue
        audio = Path(line['audio_path']).resolve()
        receipt = Path(line.get('audio_receipt_path', audio.with_suffix('.json')))
        r = json.loads(receipt.read_text(encoding="utf-8"))
        voice = {**plan.get('voice', {}), **line.get('voice', {})}
        if r.get('request') != {'text': line['text'], 'voice': voice}:
            raise ValueError('supplied narration receipt does not match current text/voice')
        if not audio.is_file() or sha256(audio) != r.get('sha256'):
            raise ValueError('supplied narration asset SHA mismatch')
        evidence.append({'audio_path':str(audio),'sha256':r['sha256'],
                         'receipt_path':str(receipt.resolve()),'receipt_sha256':sha256(receipt)})
    return evidence

def explicit_repair_round(plan):
    value=plan.get('repair_round',1)
    if type(value) is not int or value<1:raise ValueError('repair_round must be an explicit positive integer')
    return value


def prepare_item(item,audit,dest):
    q=Queue(ROOT/'output/production/queue.sqlite');parent,results=job_bundle(q,item['job_id']);override=narrative_overrides().get(item['product_id'])
    if override is None:raise ValueError('No approved narrative manifest for this product')
    if override:
        if not override['reviewed'] or sha256(override['manifest_path'])!=override['sha256']:raise ValueError('override integrity failed')
        plan=json.loads(Path(override['manifest_path']).read_text(encoding="utf-8"))
        if plan.get('parent_job_id')!=parent['id'] or plan.get('root_job_id')!=item['product_id']:raise ValueError('override is not bound to this parent/root')
        source_receipt=validate_revised_sources(plan,parent)
        write_json(dest.with_suffix('.source_validation.json'),source_receipt)
    plan.update(parent_job_id=parent['id'],root_job_id=item['product_id'],user_repair_request_id=item['request_id'],user_repair_round=explicit_repair_round(plan),use_native_foley=False,sound_effects=[],audio_policy={'mode':'independent_voice_continuous_music','allow_curated_sfx':False,'source_audio_enabled':False})
    plan.pop('prebuilt_narration',None)
    voice_evidence=verified_narration_assets(plan)
    write_json(dest.with_suffix('.voice_binding.json'),{'repair_round':explicit_repair_round(plan),'clips':voice_evidence,'parent_audio_inherited':False})
    if plan['duration_seconds']==parent['duration_seconds']:plan['prebuilt_music']={'path':results['music']['audio_path']}
    else:plan.pop('prebuilt_music',None)
    for field in ('input_sha256','workflow_version','workflow_code_sha256','controller_intent'):plan.pop(field,None)
    pilot=ROOT/'output/production/user_repair30_20261002/film_ir_shadow_pilot.json'
    if pilot.is_file():
        spec=json.loads(pilot.read_text(encoding="utf-8"))
        if spec.get('enabled') and (not spec.get('product_ids') or item['product_id'] in spec['product_ids']):
            # film_ir shadow pilot: submits the LEGACY request unchanged and records a
            # per-shot film_ir diff next to it (docs/film_ir_switch_gate.md). Opt-in by
            # flag file; absent/disabled file leaves production untouched.
            plan['shot_pipeline']='film_ir_shadow'
    from workflow.narrative_gate import validate_contract
    validate(plan);validate_contract(plan);write_json(dest,plan)
    from workflow.narrative_gate import bind_runtime_copy
    bind_runtime_copy(override['manifest_path'],override['review_evidence_path'],dest,dest.with_suffix('.narrative_review.json'))


def audit_new(item):
    spec=importlib.util.spec_from_file_location('full_shot_auditor',AUDITOR);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    record=module.run(item);write_json(OUT/'final_audits'/(item['version_job_id']+'.json'),record)

def launch(args,log,config='planning.json',lockfd=None):
    env={**os.environ,'WORKFLOW_LLM_CONFIG':str(ROOT/'workflow/config'/config)}
    # POSIX: children must inherit the lock fd to keep holding it (inode tricks).
    # Windows: locks die with the holding process anyway and pass_fds is unsupported.
    inherited=() if (lockfd is None or os.name=='nt') else (lockfd,)
    with open(log,'a') as f:return subprocess.Popen([PYTHON,str(Path(__file__).resolve()),*args],stdout=f,stderr=subprocess.STDOUT,cwd=ROOT,env=env,pass_fds=inherited)

def storyboard_review_config(root=ROOT):
    config = 'storyboard_review.json'
    if not (Path(root) / 'workflow/config' / config).is_file():
        raise FileNotFoundError('Required storyboard review configuration missing: '+config)
    return config


def automatically_preparable(request, items, overrides):
    return request['product_id'] in overrides and request['request_id'] not in items


def _render_ready(item,tasks):
    if any(t['status'] in ('failed','blocked','reconcile') for t in tasks):item.update(state='isolated',reason='execution_failed_no_blind_retry');return False
    if not tasks or any(t['status']!='succeeded' for t in tasks):return False
    return True

def _repair_finished(state,jobs,material_enabled):
    return not material_enabled(ROOT/'output/production') and len(state['items'])==30 and all(x['state'] in ('completed','isolated') for x in state['items'].values()) and not any(j not in [x.get('new_job_id') for x in state['items'].values()] for j in jobs)

def _process_items(q,state,prepare,previous,statefile,audits,lock):
    for key,item in list(state['items'].items()):
        _poll_preparation(key,item,prepare)
        if item['state']=='prepared':
            if not _submit_prepared(q,item):continue
        if item['state'] in ('rendering','auditing'):
            _poll_rendered(q,key,item,previous,statefile,state,audits,lock)


def _recover_items(state):
    for prior in state['items'].values():
        if prior.get('state')=='preparing':prior.update(state='prepared' if Path(prior['manifest_path']).exists() else 'isolated',reason='recovered_preparation_or_interrupted')
        elif prior.get('state')=='auditing' and not (OUT/'final_audits'/(prior['new_job_id']+'.json')).exists():prior.update(state='isolated',reason='audit_interrupted_no_blind_retry')

def _refresh_approvals(state):
    global OVERRIDES
    try:
        OVERRIDES=narrative_overrides();state.pop('approval_index_error',None)
    except (ValueError,OSError,KeyError) as exc:
        OVERRIDES={};state['approval_index_error']=str(exc)
    state['approved_product_count']=len(OVERRIDES)
    state['product_approval_errors']=getattr(narrative_overrides,'last_errors',{})

def _poll_preparation(key,item,prepare):
    if item['state']=='preparing' and key in prepare and prepare[key].poll() is not None:
        item['state']='prepared' if Path(item['manifest_path']).exists() else 'isolated';item['reason']='prepared' if item['state']=='prepared' else 'bounded_prompt_preparation_failed';del prepare[key]

def _submit_prepared(q,item):
    if item['product_id'] not in OVERRIDES:return False
    with q.connect() as c:r=c.execute('select * from user_repair_requests where id=?',(item['request_id'],)).fetchone()
    if not r or r['job_id']!=item['job_id'] or sha256(r['video_path'])!=r['video_sha256']:raise ValueError('request version changed')
    from workflow.narrative_gate import require_review
    bound=require_review(item['manifest_path'],Path(item['manifest_path']).with_suffix('.narrative_review.json'))
    if bound.get('runtime_binding',{}).get('reviewed_manifest_sha256')!=OVERRIDES[item['product_id']]['sha256']:
        item.update(state='isolated',reason='approved_narrative_changed_after_preparation');return False
    result=submit(q,item['manifest_path'],ROOT/'output/production',json.loads((ROOT/'research/ascend_worker_pool32.json').read_text(encoding="utf-8")));item.update(state='rendering',new_job_id=result['job_id'])
    with q.connect() as c:
        begin_immediate(c);c.execute("update user_repair_requests set status='processing' where id=? and status='pending'",(item['request_id'],))
        changed=c.execute("update production_items set current_job=?,state='watching',detail=?,updated=? where item_id=? and current_job=?",(result['job_id'],json.dumps({'action':'explicit_user_repair','request_id':item['request_id']}),time.time(),item['product_id'],item.get('resume_from_job',item['job_id']))).rowcount
        if changed!=1:raise ValueError('current product version changed before revision binding')

    return True

def _poll_rendered(q,key,item,previous,statefile,state,audits,lock):
    tasks=[t for t in q.status() if t['job_id']==item['new_job_id']]
    if not _render_ready(item,tasks):return
    if item['state']=='rendering':
        inp=OUT/'audit_inputs'/(key+'.json');write_json(inp,{'version_job_id':item['new_job_id'],'product_id':item['product_id'],'title':previous[item['product_id']]['title']});item['state']='auditing';write_json(statefile,state)
        audits[key]=launch(['--audit',str(inp)],OUT/(key+'.audit.log'),lockfd=lock.fileno())
    if (key in audits and audits[key].poll() is not None) or (key not in audits and (OUT/'final_audits'/(item['new_job_id']+'.json')).exists()):
        _finish_audit(q,item,lock)


def _finish_audit(q,item,lock):
    final=OUT/'final_audits'/(item['new_job_id']+'.json')
    if not final.exists():item.update(state='isolated',reason='full_shot_audit_failed');return
    record=json.loads(final.read_text(encoding="utf-8"));plan,results=job_bundle(q,item['new_job_id']);flags=audit_file_decision(final,[s['id'] for s in plan['shots']]);quality=results['quality'];mix=json.loads(Path(results['compose']['audio_mix_path']).read_text(encoding="utf-8"))
    # Owner decision 2026-10-07 ("扔掉审查者"): the wrap-up auditor's verdict is recorded,
    # not gating. A failed/absent review yields no flags; technical delivery gates above stay.
    if os.environ.get('WORKFLOW_NARRATIVE_FORMAT_ONLY')=='1':
        flags=[];item.setdefault('audit_waiver','format_only: auditor verdict not gating')
    passed=flags==[] and quality.get('delivery_policy',{}).get('ready_for_delivery') is True and mix.get('source_audio_in_mix') is False and mix.get('curated_effects')==[]
    if not passed:item.update(state='isolated',reason='new_full_shot_or_quality_check_failed',affected_shot_ids=flags);return
    from workflow.repair_delivery import complete
    try:
        item.update(complete(q,ROOT,item,final,lock.fileno()))
        item.pop('delivery_retry_error',None)
    except OSError as exc:
        item['delivery_retry_error']=str(exc)
    except (ValueError,KeyError) as exc:
        item.update(state='isolated',reason='strict_delivery_validation_failed',delivery_error=str(exc))

def _prepare_requests(registered,state,prepare,previous,statefile,lock):
    for request in sorted(registered,key=lambda x:x['product_id'] not in OVERRIDES):
        key=request['request_id']
        if key in state['items'] or len(prepare)>=2:continue
        if request['product_id'] not in previous:raise ValueError('unknown previous product')
        # paged360 audit layout (research/user_repair30_20261002/visual_audit.py) nests under AUDIT_VERSION;
        # legacy audits sit directly in the job folder. Prefer the newest finished record of either.
        candidates=[OUT/'visual_audit'/request['job_id']/'audit.json',
                    OUT/'visual_audit'/request['job_id']/'visual-audit-v2-paged360'/'audit.json']
        records=[json.loads(p.read_text(encoding="utf-8")) for p in candidates if p.exists()]
        audit=max(records,key=lambda r:r.get('finished_at') or 0) if records else {}
        if request['product_id'] not in OVERRIDES and not audit.get('finished_at'):continue
        item={**request,'state':'preparing','manifest_path':str(OUT/'manifests'/(key+'.json'))};state['items'][key]=item
        inp=OUT/'prepare_inputs'/(key+'.json');write_json(inp,{'item':item,'audit':audit});write_json(statefile,state)
        prepare[key]=launch(['--prepare',str(inp)],OUT/(key+'.prepare.log'),'relay.json',lock.fileno())

def _admit_additional(q,state,jobs,registered,lock):
    from workflow.storyboard_deployment import tick as storyboard_tick
    try:
        jobs+=storyboard_tick(q,ROOT/'output/production',lock.fileno())
        from workflow.repair_rollout_bridge import adopt
        adopted=adopt(ROOT/'output/production',state['items'],registered,q)
        for adopted_key in adopted:
            adopted_item=state['items'][adopted_key]
            with q.connect() as c:
                begin_immediate(c)
                c.execute("update user_repair_requests set status='processing' where id=? and job_id=? and status in ('pending','processing')",(adopted_key,adopted_item['job_id']))
        state.pop('storyboard_admission_error',None)
    except (ValueError,OSError,KeyError) as exc:
        state['storyboard_admission_error']=str(exc)
    from workflow.material_production import tick as material_tick, enabled as material_enabled
    try:
        jobs+=material_tick(q,ROOT/'output/production',lock.fileno())
        state.pop('material_admission_error',None)
    except (ValueError,OSError,KeyError,RuntimeError) as exc:
        state['material_admission_error']=str(exc)

    return material_enabled

def _update_renderer(renderer,state,jobs,control,lock):
    if renderer is not None and renderer.poll() is not None:state['renderer_failure']=renderer.returncode;renderer=None
    if jobs and renderer is None and 'renderer_failure' not in state:
        review_config=storyboard_review_config()
        renderer=launch(['--render',str(control)],OUT/'render.log',review_config,lockfd=lock.fileno())

    return renderer

def _publish_progress(state,renderer,statefile,material_enabled,jobs,control):
    state.update(updated_at=time.time(),renderer_pid=renderer.pid if renderer else None,completed=sum(x['state']=='completed' for x in state['items'].values()),isolated=sum(x['state']=='isolated' for x in state['items'].values()),total=30);write_json(statefile,state)
    if _repair_finished(state,jobs,material_enabled):
        write_json(control,{'jobs':jobs,'stop':True});state['stage']='finished_with_explicit_outcomes';write_json(statefile,state);return True
    return False

def main():
    global OVERRIDES
    OVERRIDES=narrative_overrides()
    subprocess.run([PYTHON,'-c','import numpy,edge_tts'],check=True)
    q=Queue(ROOT/'output/production/queue.sqlite');statefile=OUT/'driver_status.json'
    with open(q.path+'.controller-service.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if paused(ROOT/'output/production'):raise RuntimeError('Production is paused; root must explicitly enable user repair')
        state=json.loads(statefile.read_text(encoding="utf-8")) if statefile.exists() else {'items':{},'started_at':time.time()}
        state.update(pid=os.getpid(),stage='running');prepare={};audits={};renderer=None
        _recover_items(state)
        registered=json.loads((OUT/'status.json').read_text(encoding="utf-8"))['items'];previous={x['product_id']:x for x in json.loads((OUT/'previous_delivery_index.json').read_text(encoding="utf-8"))['items']}
        while True:
            _refresh_approvals(state)
            try:
                _process_items(q,state,prepare,previous,statefile,audits,lock)
                _prepare_requests(registered,state,prepare,previous,statefile,lock)
                jobs=[x['new_job_id'] for x in state['items'].values() if x.get('new_job_id')]
                material_enabled=_admit_additional(q,state,jobs,registered,lock)
            except sqlite3.OperationalError as exc:
                # Lock contention must degrade to a retry, never kill the driver.
                state['last_lock_error']=str(exc)[:80];state['lock_error_count']=state.get('lock_error_count',0)+1
                write_json(statefile,state);time.sleep(8);continue
            control=OUT/'render_jobs.json';write_json(control,{'jobs':jobs,'stop':False})
            renderer=_update_renderer(renderer,state,jobs,control,lock)
            if _publish_progress(state,renderer,statefile,material_enabled,jobs,control):return
            time.sleep(3)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',action='store_true');parser.add_argument('--prepare',type=Path);parser.add_argument('--audit',type=Path);parser.add_argument('--render',type=Path);args=parser.parse_args()
    if args.prepare:
        data=json.loads(args.prepare.read_text(encoding="utf-8"));prepare_item(data['item'],data['audit'],Path(data['item']['manifest_path']))
    elif args.audit:audit_new(json.loads(args.audit.read_text(encoding="utf-8")))
    elif args.render:
        import scripts.run_deadline30 as renderer_module
        renderer_module.OUT=OUT;renderer_module.render_jobs(args.render)
    elif args.run:main()
    else:print('Prepared, not started. Explicit start: .venv-workflow/bin/python -u scripts/run_user_repair30.py --run')
