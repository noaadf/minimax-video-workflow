"""Selective visual repair with a finite budget; factual uncertainty never auto-rewrites history."""
import copy
import json
from pathlib import Path
from .llm import ask_json
from .core.io import digest, sha256, write_json
from .plan import validate

VISUAL={'geometry','visual_quality','visual_repetition','visual_text_mismatch'}


def repair_decision(plan,review,max_rounds=2):
    if plan.get('quality_profile')=='throughput_v1':
        from .delivery_policy import is_blocking
        max_rounds=min(max_rounds,1)
        material=[f for f in review.get('findings',[]) if is_blocking(f)]
        if not material:return {'action':'none','reason':'minor findings are advisory under throughput policy'}
        review={**review,'findings':material}
    if int(plan.get('repair_round',0))>=max_rounds:return {'action':'hold','reason':'repair budget exhausted'}
    if review.get('verdict')=='pass':return {'action':'none','reason':'already passed'}
    findings=[f for f in review.get('findings',[]) if f.get('severity') in ('high','medium')]
    if not findings:return {'action':'hold','reason':'no actionable visual findings'}
    valid={s['id'] for s in plan['shots']};affected=set()
    for f in findings:
        if f.get('category') not in VISUAL:return {'action':'hold','reason':'factual, audio, subtitle or unclassified finding requires a different repair route'}
        ids=f.get('shot_ids',[])
        if not ids or not set(ids)<=valid:return {'action':'hold','reason':'finding cannot be attributed to existing shots'}
        affected.update(ids)
    return {'action':'regenerate','shot_ids':sorted(affected),'findings':findings}


def job_bundle(queue,jid):
    with queue.connect() as c:
        row=c.execute('SELECT manifest FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row:raise ValueError('unknown job')
        tasks=[dict(x) for x in c.execute('SELECT * FROM tasks WHERE job_id=?',(jid,))]
    if any(t['status']!='succeeded' for t in tasks):raise ValueError('repair only accepts completed jobs')
    results={t['id'].split(':',1)[1]:json.loads(t['result']) for t in tasks}
    for r in results.values():
        for path,checksum in r.get('sha256',{}).items():
            if not Path(path).is_file() or sha256(path)!=checksum:raise ValueError('repair input artifact changed')
    manifest=results['compose'].get('manifest_path')
    plan=json.loads(Path(manifest).read_text(encoding="utf-8")) if manifest else json.loads(row['manifest'])
    return plan,results


def build_revision(plan,results,patches,decision):
    if len(patches)!=len(decision['shot_ids']) or {p.get('id') for p in patches}!=set(decision['shot_ids']):
        raise ValueError('repair may change only flagged shot prompts, exactly once each')
    patch_map={p['id']:p for p in patches};p=copy.deepcopy(plan)
    root=p.get('root_job_id',p['id']);round_no=int(p.get('repair_round',0))+1
    p['parent_job_id']=p['id'];p['root_job_id']=root;p['repair_round']=round_no
    p['id']=root[:55]+'-repair'+str(round_no)+'-'+digest(patches)[:8]
    for s in p['shots']:
        if s['id'] in patch_map:
            patch=patch_map[s['id']]
            if set(patch)!={'id','prompt'} or not isinstance(patch['prompt'],str) or not 20<=len(patch['prompt'])<=6000:
                raise ValueError('invalid visual repair patch')
            s['prompt']=patch['prompt'];s['seed']=(int(s.get('seed',1))+round_no*100003)%(2**31)
            s.pop('source_path',None);s.pop('source_start',None);s.pop('foley_source_path',None)
        else:
            s['source_path']=results[s['id']]['video_path'];s['source_start']=0
            if results[s['id']].get('foley_path'):s['foley_source_path']=results[s['id']]['foley_path']
    p['prebuilt_narration']={'path':results['voice']['audio_path'],
                            'effective_narration':results['voice']['effective_narration'],
                            'segments':results['voice']['segments']}
    for field in ('input_sha256','workflow_version','workflow_code_sha256'):p.pop(field,None)
    validate(p)
    return p


def prepare(queue,jid,folder,max_rounds=2,client=ask_json):
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    plan,results=job_bundle(queue,jid)
    review=results['quality'].get('editorial',{}).get('review',{})
    decision=repair_decision(plan,review,max_rounds)
    record=folder/(jid+'.json')
    if record.exists():
        previous=json.loads(record.read_text(encoding="utf-8"))
        if previous.get('action')=='prepared':return previous
    if decision['action']!='regenerate':write_json(record,decision);return decision
    backend=plan.get('backend',{})
    from .backends import ready
    if backend.get('type') not in ('ascend_ssh','pool') or not ready(backend):
        result={'action':'hold','reason':'generation backend is not verified ready','decision':decision}
        write_json(record,result);return result
    selected=[s for s in plan['shots'] if s['id'] in decision['shot_ids']]
    response,receipt=client('Repair only the selected video prompts according to visual quality findings. Preserve facts, duration, camera role, structural identity and continuity bible. Use concrete visual mechanics and natural documentary color: restrained saturation, neutral white balance, plausible skin/material colors, no default golden-hour or teal-orange advertising grade. Preserve physically justified scene lighting. Do not modify narration, facts, technical measurements, source attribution or other shots. Return {patches:[{id,prompt}]} for every selected shot exactly once. Prompts in English; no text/logos generated.',
                            {'facts':plan['facts'],'continuity_bible':plan['continuity_bible'],'shots':selected,'findings':decision['findings']})
    revised=build_revision(plan,results,response.get('patches',[]),decision)
    path=folder/(revised['id']+'.manifest.json');write_json(path,revised)
    result={'action':'prepared','parent_job_id':jid,'job_id':revised['id'],'manifest_path':str(path),
            'regenerated_shots':decision['shot_ids'],'reused_shots':[s['id'] for s in plan['shots'] if s['id'] not in decision['shot_ids']],
            'repair_round':revised['repair_round'],'receipt':receipt}
    write_json(record,result);return result


def repair_batch(queue,root,max_rounds=2):
    from .runner import submit
    states=queue.status();jobs=sorted({t['job_id'] for t in states})
    reports=[]
    for jid in jobs:
        rows=[t for t in states if t['job_id']==jid]
        if any(t['status']!='succeeded' for t in rows):continue
        qc=next((json.loads(t['result']) for t in rows if t['stage']=='quality'),{})
        if qc.get('editorial_status')=='automatic_review_passed':continue
        try:
            r=prepare(queue,jid,Path(root)/'repairs',max_rounds)
            if r['action']=='prepared':
                p=json.loads(Path(r['manifest_path']).read_text(encoding="utf-8"))
                r={**r,'submission':submit(queue,r['manifest_path'],root,p['backend'])}
            reports.append(r)
        except Exception as e:reports.append({'parent_job_id':jid,'action':'hold','reason':str(e)[:250]})
    write_json(Path(root)/'repair_status.json',reports)
    return reports
