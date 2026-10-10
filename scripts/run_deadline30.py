"""Durable single-owner streaming production; catalogs are data, never commands."""
import argparse, json, os, subprocess, sys, time
from datetime import datetime
from itertools import zip_longest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
# fcntl must come after ROOT joins sys.path: stdlib on POSIX, ROOT/fcntl.py shim on Windows.
import fcntl
from workflow.plan import write_json,digest
from workflow.queue import Queue
from workflow.production_control import paused
OUT=ROOT/'output/production/deadline30_20261002'
LOCK_FD=None
_VENVPY=ROOT/'.venv-workflow'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
PYTHON=str(_VENVPY if _VENVPY.exists() else ROOT/'.venv-workflow/bin/python')
CPU_MEDIA_STAGES=frozenset({'shot','voice','music','story_preflight','compose','export'})

def preflight():
    subprocess.run([PYTHON,'-c','import numpy,edge_tts'],check=True,timeout=30)

def ordered_catalogs():
    base=ROOT/'research/deadline30_20261002'
    recent=candidates([base/'catalog_c.json'])
    groups=[candidates([base/n]) for n in ('catalog_a.json','catalog_b.json')]
    merged=recent+[t for pair in zip_longest(*groups) for t in pair if t is not None]
    merged+=candidates([ROOT/'output/production/distinct_batch_20261002/catalog.json'])
    seen=set();result=[]
    for t in merged:
        key=t.get('story_key') or t['title'];url=t['source_url']
        if key in seen or url in seen:continue
        seen.update((key,url));result.append(t)
    return result

def candidates(paths):
    seen=set();items=[]
    for path in paths:
        if not path.exists():continue
        try:data=json.loads(path.read_text(encoding="utf-8"))
        except (ValueError,OSError):continue
        for t in data.get('topics',[]):
            url=t.get('source_url','')
            key=t.get('story_key') or t.get('title','').strip()
            if t.get('status')!='source_supported' or not url.startswith('https://') or not t.get('facts') or url in seen or key in seen:continue
            seen.update((url,key));items.append(t)
    return items

def counts(rows,jobs,identities=None):
    good=set();exports=set();failures=[]
    for t in rows:
        if t['job_id'] not in jobs:continue
        if t['status'] in ('failed','reconcile'):failures.append({'job_id':t['job_id'],'stage':t['stage'],'state':t['status'],'error':t.get('error')})
        if t['status']!='succeeded':continue
        if t['stage']=='export':exports.add(t['job_id'])
        if t['stage']=='quality':
            r=json.loads(t.get('result') or '{}')
            if r.get('delivery_policy',{}).get('ready_for_delivery') is True:good.add(t['job_id'])
    identities=identities or {}
    delivered={identities.get(j,j) for j in good & exports}
    return {'ready_count':len(delivered),'ready_jobs':sorted(good & exports),'exported_count':len(exports),'failures':failures}

def plan_one(path):
    from workflow.batch import produce
    cfg=json.loads((ROOT/'workflow/config/production.json').read_text(encoding="utf-8"));cfg['limit']=1
    cfg['requirements']+=' 本条必须讲清一个独立故事，避免参数罗列和泛泛重复；按事件安排镜头数量和时长，最后一句之后至少2秒音乐尾声。'
    from workflow.plan import generate
    def planner(brief,dest):
        brief['quality_profile']=cfg.get('quality_profile','throughput_v1')
        plan=generate(brief,dest)
        plan['quality_profile']=brief['quality_profile']
        return plan
    result=produce(Queue(ROOT/'output/production/queue.sqlite'),{'topics':[json.loads(path.read_text(encoding="utf-8"))]},cfg,ROOT/'output/production',planner=planner)
    write_json(path.with_suffix('.result.json'),result)

def render_jobs(path):
    """One resident renderer; each lane refreshes admitted jobs without batch barriers."""
    preflight()
    import numpy,edge_tts
    from workflow import media,shot_review,storyboard,review
    from workflow.first_frame import runtime_requirements
    write_json(OUT/'renderer_runtime.json',{'pid':os.getpid(),'sys_executable':sys.executable,'prefix':sys.prefix,'numpy':numpy.__version__,'edge_tts_imported':True,'at':time.time(),'normalization_version':media.NORMALIZATION_VERSION,'loaded_media_sha256':media.MEDIA_CODE_SHA256,'story_review_version':shot_review.VERSION,'story_preflight_version':storyboard.PREFLIGHT_VERSION,'story_preflight_lane':True,'visual_style_review_version':review.VISUAL_STYLE_REVIEW_VERSION,'loaded_review_sha256':review.REVIEW_CODE_SHA256,**runtime_requirements()})
    import threading,concurrent.futures
    from workflow.runner import work
    from workflow.backends import members,check_lease
    q=Queue(ROOT/'output/production/queue.sqlite')
    cfg=json.loads((ROOT/'workflow/config/production.json').read_text(encoding="utf-8"));groups=members(json.loads(Path(cfg['backend_file']).read_text(encoding="utf-8")))
    for g in groups:check_lease(g)
    caps={'cpu',*[g['capability'] for g in groups]};stop=threading.Event()
    class LiveJobs:
        current=frozenset()
        def __contains__(self,key):return key in self.current
    jobs=LiveJobs()
    def refresh():
        while not stop.is_set():
            try:
                control=json.loads(path.read_text(encoding="utf-8"));jobs.current=frozenset(control['jobs'])
                if control.get('stop'):stop.set();return
            except (OSError,ValueError):pass
            stop.wait(.5)
    threading.Thread(target=refresh,daemon=True).start()
    def lane(cap,stages):
        while not stop.is_set():
            try:work(q,cap,stop,jobs,stages,caps)
            except Exception as exc:
                print(json.dumps({'event':'renderer_lane_failed','capabilities':cap,'error_type':type(exc).__name__,'error':str(exc)[:500]}),flush=True)
                stop.set();raise
            stop.wait(.5)
    lanes=[([c],{'shot'}) for c in sorted(caps-{'cpu'})]
    lanes += [(['cpu'],CPU_MEDIA_STAGES)]*3
    lanes += [(['cpu'],{'quality'})]*2+[(['cpu'],{'speech'})]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(lanes)) as pool:
        futures=[pool.submit(lane,*x) for x in lanes]
        for f in futures:f.result()

def launch(args,config,log):
    env={**os.environ,'WORKFLOW_LLM_CONFIG':str(ROOT/'workflow/config'/config)}
    with open(log,'a') as f:return subprocess.Popen([PYTHON,str(Path(__file__).resolve()),*args],cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,pass_fds=(() if LOCK_FD is None else (LOCK_FD,)))

def _known_jobs(q,state):
    jobs=sorted({r['job_id'] for i in state['items'].values() for r in i.get('results',[]) if r.get('state') in ('enqueued','deduplicated')})
    # Recognize already-committed queue jobs while their planner is finishing.
    with q.connect() as c:
        known={r['id'] for r in c.execute('SELECT id FROM jobs')}
    jobs=sorted(set(jobs)|{i['job_id'] for i in state['items'].values() if i.get('job_id') in known})
    return jobs

def _start_renderer(ispaused,state,render,active,jobs,render_since):
    if not ispaused and not state.get('renderer_failure') and render is None and active:
        # Never claim a task with ambiguous previous external execution.
        rp=OUT/'render_jobs.json';write_json(rp,{'jobs':jobs,'stop':False});render=launch(['--render',str(rp)],'planning.json',OUT/'render.log');render_since=time.time()
    return render,render_since

def _controller_jobs(controller,jobs):
    root_jobs=set(jobs)
    children={x['current_job']:x['item_id'] for x in controller.items() if x['item_id'] in root_jobs}
    jobs=sorted(root_jobs|set(children))
    return jobs,children

def _recover_planners(state):
    # A crashed planner is never silently reissued; inspect its committed result/DB.
    for key,item in state['items'].items():
        if item.get('state')=='planning':
            rp=Path(item['topic_path']).with_suffix('.result.json')
            item['state']='planning_interrupted'
            if rp.exists():item.update(state='finished_planning',results=json.loads(rp.read_text(encoding="utf-8")))

def _finish_planners(planners,state):
    for key,p in list(planners.items()):
        if p.poll() is None:continue
        item=state['items'][key];rp=Path(item['topic_path']).with_suffix('.result.json')
        item.update(state='finished_planning' if rp.exists() else 'planning_failed',exit_code=p.returncode,finished_at=time.time())
        if rp.exists():item['results']=json.loads(rp.read_text(encoding="utf-8"))
        del planners[key]

def _admit_jobs(q,state,controller):
    jobs=_known_jobs(q,state)
    for j in jobs:
        if j in state.get('admitted',[]):continue
        try:controller.admit({'id':'deadline30_'+j,'items':[{'id':j,'job_id':j,'max_repairs':1}]})
        except ValueError as e:
            if 'already owned' not in str(e):state.setdefault('admission_errors',{})[j]=str(e);continue
        state.setdefault('admitted',[]).append(j)

    return jobs

def _poll_renderer(render,state):
    if render is not None and render.poll() is not None:
        state['last_renderer']={'exit_code':render.returncode,'finished_at':time.time()}
        if render.returncode!=0:state['renderer_failure']={'exit_code':render.returncode,'at':time.time(),'action':'diagnose_before_restart'}
        render=None

    return render

def _plan_topics(alltopics,ispaused,state,planners,metrics,active,statefile):
    for topic in alltopics:
        if ispaused or state.get('renderer_failure') or len(planners)>=2 or metrics['ready_count']>=30:break
        key=digest(topic['source_url'])[:16]
        if key in state['items']:continue
        # Stop new admission at 30 potential successes, replace only terminal failures.
        potential=metrics['ready_count']+len(active)+len(planners)
        if potential>=30:break
        path=OUT/'topics'/(key+'.json');write_json(path,topic)
        state['items'][key]={'state':'planning','topic_path':str(path),'title':topic['title'],'job_id':'production_'+key,'started_at':time.time()}
        write_json(statefile,state)
        planners[key]=launch(['--plan',str(path)],'relay.json',OUT/(key+'.log'))

def _report_deadline(state,metrics,active,planners):
    if time.time()>=datetime.fromisoformat(state['deadline']).timestamp() and not state.get('deadline_report'):
        state['deadline_report']={'at':time.time(),'ready_count':metrics['ready_count'],'target':30,'uncompleted':max(0,30-metrics['ready_count']),'active_jobs':sorted(active),'planning_count':len(planners),'met':metrics['ready_count']>=30}
        write_json(OUT/'deadline_report.json',state['deadline_report'])
        print(json.dumps({'event':'deadline_reached',**state['deadline_report']}),flush=True)

def _update_progress(state,metrics,ispaused,active,planners,alltopics,render,render_since,jobs):
    state.update(metrics=metrics,stage='paused' if ispaused else ('complete' if metrics['ready_count']>=30 and not active and not planners else 'running'),updated_at=time.time(),catalog_count=len(alltopics),planning_pids=[p.pid for p in planners.values()],renderer_pid=render.pid if render else None,renderer_started_at=render_since,jobs=jobs)

def main():
    global LOCK_FD
    preflight()
    OUT.mkdir(parents=True,exist_ok=True)
    with open(OUT/'driver.lock','a') as own:
        try:fcntl.flock(own,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        q=Queue(ROOT/'output/production/queue.sqlite');statefile=OUT/'status.json'
        state=json.loads(statefile.read_text(encoding="utf-8")) if statefile.exists() else {'items':{},'started_at':time.time()}
        state.update(pid=os.getpid(),stage='waiting_for_existing_controller',target=30,deadline='2026-10-02T22:00:00+08:00');write_json(statefile,state)
        with open(q.path+'.controller-service.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            LOCK_FD=lock.fileno()
            from workflow.controller import Controller
            controller=Controller(q,ROOT/'output/production')
            planners={};render=None;render_since=None
            _recover_planners(state)
            while True:
                ispaused=paused(ROOT/'output/production')
                _finish_planners(planners,state)
                jobs=_admit_jobs(q,state,controller)
                controller.tick()
                jobs,children=_controller_jobs(controller,jobs)
                rows=q.status();metrics=counts(rows,jobs,children)
                render=_poll_renderer(render,state)
                active={t['job_id'] for t in rows if t['job_id'] in jobs and t['status'] in ('pending','running')}
                render,render_since=_start_renderer(ispaused,state,render,active,jobs,render_since)
                write_json(OUT/'render_jobs.json',{'jobs':jobs,'stop':False})
                alltopics=ordered_catalogs()
                _plan_topics(alltopics,ispaused,state,planners,metrics,active,statefile)
                _update_progress(state,metrics,ispaused,active,planners,alltopics,render,render_since,jobs)
                _report_deadline(state,metrics,active,planners)
                write_json(statefile,state)
                if state['stage']=='complete':
                    write_json(OUT/'render_jobs.json',{'jobs':jobs,'stop':True})
                    if render is not None:render.wait(timeout=60)
                    break
                time.sleep(5)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path);p.add_argument('--render',type=Path);a=p.parse_args()
    if a.plan:plan_one(a.plan)
    elif a.render:render_jobs(a.render)
    else:main()
