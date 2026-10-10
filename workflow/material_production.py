"""Automatic material intake inside the existing singleton production owner.

Finite children prepare media only. The existing owner alone admits jobs and
returns them to its existing renderer. Durable intents never replay model calls.
"""
import fcntl, json, os, subprocess, sys, time
from pathlib import Path
from .core.io import digest, sha256
from .external_image_executor import save
from .material_preparation import read, verified_request

VERSION='material-production-v2-source-story'
_CHILDREN={}

def policy(root):
    p=Path(root)/'material_intake/production_policy.json'
    return read(p) if p.exists() else {'enabled':False}

def enabled(root):return policy(root).get('enabled') is True

def require_owner(queue,fd):
    expected=Path(queue.path+'.controller-service.lock').stat();actual=os.fstat(fd) if fd is not None else None
    if actual is None or (actual.st_dev,actual.st_ino)!=(expected.st_dev,expected.st_ino):
        raise ValueError('Existing singleton owner lock required')
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)

def alive(pid):
    try:os.kill(int(pid),0);return True
    except (ProcessLookupError,ValueError,TypeError):return False


def verify_ready(result,folder,request_sha):
    from .narrative_gate import require_review
    from .storyboard import reuse_preflight
    from .plan import freeze_assets,validate
    inspection=folder/'manual_picture_inspection.json'
    if inspection.exists() and read(inspection).get('verdict')=='reject':
        raise ValueError('Actual keyframe inspection rejected this preparation version')
    if result.get('state')!='ready' or result.get('request_sha256')!=request_sha:
        raise ValueError('Preparation is not committed for this exact request')
    for key in ('manifest','review','preflight'):
        p=Path(result[key+'_path']).resolve()
        if not p.is_relative_to(folder) or sha256(p)!=result[key+'_sha256']:
            raise ValueError('Prepared evidence changed: '+key)
    p=freeze_assets(validate(read(result['manifest_path'])),Path(result['manifest_path']).parent)
    require_review(result['manifest_path'],result['review_path'])
    binding=p.get('approved_story_preflight',{})
    if binding!={'path':result['preflight_path'],'sha256':result['preflight_sha256']}:
        raise ValueError('Prepared preflight binding differs')
    if p['id']!=result['job_id']:raise ValueError('Prepared job identity differs')
    reuse_preflight(p,{'audio_path':p['prebuilt_narration']['path'],'effective_narration':p['narration']},
                   {'audio_path':p['prebuilt_music']['path']},folder/'owner_verification')
    return p


def _discover_requests(root, queue, items, directory, now):
    # Validate one request at a time; broken or revised requests never starve peers.
    for request in sorted((root/'material_intake').glob('*/production_request.json')):
        key=digest([request.parent.name,sha256(request)])[:24]
        if key in items:continue
        try:r=verified_request(request,queue)
        except (ValueError,OSError,KeyError) as exc:
            items[key]={'content_id':request.parent.name,'state':'attention','request_path':str(request),'request_sha256':sha256(request),'reason':str(exc),'automatic_retry':False};continue
        items[key]={'content_id':r['content_id'],'title':read(r['manifest_path'])['title'],'request_path':str(request),'request_sha256':sha256(request),'state':'waiting','folder':str(directory/key),'received':now}


def _preparation_running(key, item):
    child=_CHILDREN.get(key)
    if child is not None:
        returncode=child.poll()
        running=returncode is None
        if not running:_CHILDREN.pop(key,None)
    else:running=alive(item.get('pid'))
    return running


def _admit_prepared(queue, root, backend, key, item, folder, commit):
    result_path=Path(item['result_path'])
    if sha256(result_path)!=item['result_sha256']:raise ValueError('Committed preparation result changed')
    result=read(result_path);plan=verify_ready(result,folder,item['request_sha256']);jid=result['job_id']
    # Persist before the queue transaction. Recover the same committed job, never clone it.
    if item['state']=='prepared':item.update(state='admitting',job_id=jid,admission_intent_at=time.time());commit()
    with queue.connect() as c:existing=c.execute('SELECT manifest FROM jobs WHERE id=?',(jid,)).fetchone()
    if existing:
        queued=json.loads(existing['manifest'])
        if queued.get('material_origin')!=plan['material_origin'] or queued.get('approved_story_preflight')!=plan['approved_story_preflight']:
            raise ValueError('Existing job is not this committed material request')
        # Validate authored fields and all frozen assets across crash recovery.
        from .narrative_gate import AUTHORED_FIELDS
        if any(queued.get(k)!=plan.get(k) for k in AUTHORED_FIELDS) or queued.get('input_sha256')!=plan.get('input_sha256'):
            raise ValueError('Queued material differs from committed preparation')
    else:
        from .runner import submit
        submit(queue,result['manifest_path'],root,backend)
    from .controller import Controller
    Controller(queue,root).admit({'id':'material_'+key,'items':[{'id':jid,'job_id':jid,'max_repairs':0}]})
    item.update(state='rendering',admitted_at=time.time());commit()


def _check_rendering(queue, key, item, jobs):
    tasks=[t for t in queue.status() if t['job_id']==item['job_id']]
    if any(t['status'] in ('failed','blocked','reconcile') for t in tasks):raise ValueError('Execution needs diagnosis; no blind model retry')
    if tasks and all(t['status']=='succeeded' for t in tasks):
        from .repair import job_bundle
        _,results=job_bundle(queue,item['job_id'])
        if results['quality'].get('delivery_policy',{}).get('ready_for_delivery') is not True:
            raise ValueError('Final quality policy did not pass')
        item.update(state='ready_for_human_review',video_path=results['compose']['video_path'],production_approved=False)
        with queue.connect() as c:c.execute("UPDATE production_items SET state='review',detail=?,updated=? WHERE batch_id=? AND item_id=?",(json.dumps({'action':'material_ready_for_human_review','content_id':item['content_id']}),time.time(),'material_'+key,item['job_id']))
    else:jobs.append(item['job_id'])


def tick(queue,root,owner_lock_fd):
    root=Path(root).resolve();cfg=policy(root)
    if cfg.get('enabled') is not True:return []
    require_owner(queue,owner_lock_fd)
    from .production_control import paused
    if paused(root):return []
    directory=root/'material_intake/production';directory.mkdir(parents=True,exist_ok=True)
    state_path=directory/'state.json'
    state=read(state_path) if state_path.exists() else {'version':VERSION,'items':{}}
    for key,child in list(_CHILDREN.items()):
        if child.poll() is not None:_CHILDREN.pop(key,None)
    items=state['items'];now=time.time()
    def commit():
        state.update(version=VERSION,owner_pid=os.getpid(),updated=time.time());save(state_path,state)
    _discover_requests(root, queue, items, directory, now)
    commit()
    active=sum(x['state']=='preparing' for x in items.values())
    limit=cfg.get('max_preparing',2)
    if type(limit) is not int or not 1<=limit<=2:raise ValueError('Preparation concurrency must be 1..2')
    backend=read(cfg['backend_file']);jobs=[]
    # Prefer new generated scenes; source-only stories remain eligible, too.
    ordered=sorted(items.items(),key=lambda pair:(pair[1].get('received',0),pair[0]))
    for key,item in ordered:
        if item['state'] in ('attention','completed','ready_for_human_review'):continue
        folder=Path(item['folder']);request=Path(item['request_path'])
        try:
            if sha256(request)!=item['request_sha256']:raise ValueError('Request changed during preparation')
            verified_request(request,queue)
            if item['state']=='waiting' and active<limit:
                folder.mkdir(parents=True,exist_ok=True)
                # Persist launch intent before creating a child; interrupted launch is attention.
                item.update(state='launch_intent',launch_at=time.time());commit()
                env={**os.environ,'WORKFLOW_LLM_CONFIG':cfg['review_config']}
                with (folder/'worker.log').open('a') as log:
                    # pass_fds is POSIX-only; on Windows the lock dies with us anyway.
                    _inherit=() if os.name=='nt' else (owner_lock_fd,)
                    child=subprocess.Popen([sys.executable,'-u','-m','workflow.material_preparation','--request',str(request),'--folder',str(folder)],cwd=Path(__file__).resolve().parents[1],env=env,stdout=log,stderr=subprocess.STDOUT,pass_fds=_inherit)
                _CHILDREN[key]=child;item.update(state='preparing',pid=child.pid);active+=1;commit()
            elif item['state']=='launch_intent':
                raise ValueError('Interrupted child launch requires diagnosis; no automatic replay')
            if item['state']=='preparing':
                running=_preparation_running(key,item)
                result_path=folder/'result.json'
                if result_path.exists():
                    result=read(result_path);verify_ready(result,folder,item['request_sha256']);item.update(state='prepared',result_path=str(result_path),result_sha256=sha256(result_path));active-=1;commit()
                elif not running:
                    worker=read(folder/'worker_status.json') if (folder/'worker_status.json').exists() else {}
                    raise ValueError(worker.get('error','Preparation interrupted; inspect durable attempt and remote receipts'))
                else:
                    item['phase']=read(folder/'worker_status.json').get('phase') if (folder/'worker_status.json').exists() else 'launching';continue
            if item['state'] in ('prepared','admitting'):
                _admit_prepared(queue, root, backend, key, item, folder, commit)
            if item['state']=='rendering':
                _check_rendering(queue, key, item, jobs)
        except (ValueError,OSError,KeyError,RuntimeError) as exc:
            if item['state']=='preparing':active-=1
            item.update(state='attention',reason=str(exc),automatic_retry=False,attention_at=time.time())
        commit()
    commit()
    return jobs
