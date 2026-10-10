"""Catalog-to-queue orchestration with persistent source deduplication."""
import json
import time
from pathlib import Path
from .core.io import digest, write_json
from .plan import generate
from .runner import submit,backend_ready

THEMES={'engineering':'engineering_tension','science':'science_reveal','product':'product_transformation','global':'global_connection'}


def produce(queue,catalog,config,root,planner=generate):
    root=Path(root).resolve();folder=root/'batch_plans';folder.mkdir(parents=True,exist_ok=True)
    with queue.connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS editorial_items(
            source_url TEXT PRIMARY KEY, job_id TEXT NOT NULL, topic_digest TEXT NOT NULL,
            state TEXT NOT NULL, lease_until REAL, manifest_path TEXT, error TEXT)''')
    selected=[];seen=set()
    # One topic per theme first; deterministic fill then favors supplied visual score.
    ranked=sorted(catalog.get('topics',[]),key=lambda t:(-t.get('visual_potential',0),t['id']))
    for t in ranked:
        if t.get('status')=='source_supported' and t.get('theme') in THEMES and t['theme'] not in seen:
            selected.append(t);seen.add(t['theme'])
    selected += [t for t in ranked if t.get('status')=='source_supported' and t not in selected]
    selected=selected[:max(1,min(20,int(config.get('limit',3))))]
    results=[]
    for topic in selected:
        url=topic['source_url'];identity=digest(url)[:16];jid='production_'+identity
        with queue.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM editorial_items WHERE source_url=?',(url,)).fetchone()
            if old and (old['state']=='enqueued' or (old['state']=='planning' and old['lease_until']>time.time())):
                results.append({'source_url':url,'state':'deduplicated','job_id':old['job_id']});continue
            c.execute('INSERT OR REPLACE INTO editorial_items VALUES(?,?,?,?,?,?,?)',
                      (url,jid,digest(topic),'planning',time.time()+1200,None,None))
        path=folder/(jid+'.json')
        brief={'id':jid,'template':THEMES[topic['theme']],'topic':topic['title'],'facts':topic['facts'],
               'music':config['music'],'requirements':config.get('requirements','快速悬念开头，真实技术细节、多视角，宏大而具体，保留收尾余音，评论提问自然。'),
               'duration_policy':config.get('duration_policy',{'mode':'content_driven','min_seconds':15,'max_seconds':120}),'source_cautions':topic.get('cautions',[])}
        brief['pacing_policy'] = config.get('pacing_policy', 'storyboard_v1')
        if brief['pacing_policy'] == 'storyboard_v1':
            brief['caption_policy'] = 'speech_aligned_v1'
        try:
            if config.get('audiovisual_library_file'):
                from .profiles import select,apply
                apply(brief,select(config['audiovisual_library_file'],url,brief['template'],config.get('audiovisual_profile')))
            if config.get('style_library_file'):
                library=json.loads(Path(config['style_library_file']).expanduser().read_text(encoding="utf-8"))
                variants=[r for r in library.get('variants',[]) if brief['template'] in r.get('compatible_templates',[])]
                recipe=variants[int(digest(url),16)%len(variants)] if variants else library.get('recipes',{}).get(brief['template'])
                if recipe:
                    brief['style_reference']={'scope':library.get('scope'),**recipe}
            if path.exists():
                plan=json.loads(path.read_text(encoding="utf-8"))
                if plan.get('facts')!=topic['facts']:raise ValueError('source changed after planning; require a new explicit edition')
            else:
                plan=planner(brief,path)
            if config.get('require_engagement_review', False):
                from .engagement import enrich
                enrich(plan)
            plan['voice_policy']=config.get('voice_policy',{})
            if plan['voice_policy'].get('natural_pitch'):
                plan.setdefault('voice',{})['pitch']='+0Hz'
                for line in plan.get('narration',[]):
                    if 'voice' in line:line['voice']['pitch']='+0Hz'
            plan['review_mode']='api'
            plan['quality_profile']=config.get('quality_profile','strict')
            plan['use_native_foley']=False
            plan['audio_policy']={'mode':'independent_voice_continuous_music','allow_curated_sfx':False,'source_audio_enabled':False}
            plan['source_packet']={'source_url':url,'published':topic['published'],'archive':topic.get('source_archive'),'verification_scope':topic.get('verification_scope')}
            write_json(path,plan)
            backend=config.get('backend')
            if config.get('backend_file'):
                backend=json.loads(Path(config['backend_file']).expanduser().read_text(encoding="utf-8"))
            from .storyboard import enabled, missing_previsuals
            missing = missing_previsuals(plan, path.parent) if enabled(plan) else []
            if missing:
                state='awaiting_storyboard';result={'job_id':jid,'state':state,'manifest_path':str(path),'missing':missing}
                from .storyboard import write_keyframe_requests
                result['keyframe_request_path']=write_keyframe_requests(plan,path,path.with_suffix('.keyframes.json'))
            elif not backend or not backend_ready(backend):
                state='awaiting_backend';result={'job_id':jid,'state':state,'manifest_path':str(path)}
            else:
                result=submit(queue,path,root,backend);state='enqueued';result['state']=state
            with queue.connect() as c:
                c.execute('UPDATE editorial_items SET state=?,lease_until=NULL,manifest_path=?,error=NULL WHERE source_url=?',(state,str(path),url))
            results.append(result)
        except Exception as e:
            with queue.connect() as c:
                c.execute('UPDATE editorial_items SET state=?,lease_until=NULL,error=? WHERE source_url=?',('failed',str(e)[:300],url))
            results.append({'source_url':url,'job_id':jid,'state':'failed','reason':str(e)[:300]})
    write_json(root/'batch_status.json',results)
    return results
