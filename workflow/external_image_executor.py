"""Finite MiniMax image-01 executor for existing ImageJobs candidates.

No implicit retry of submissions. A durable intent without a response is unknown,
not pending. Arbitrary-object references are unsupported and never dropped.
"""
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from PIL import Image
from .anthropic_client import credential
from .narrative_gate import require_review
from .core.io import sha256
from .storyboard_image_jobs import ImageJobs, PHOTOREAL_POLICY

URL='https://api.minimaxi.com/v1/image_generation'
MAX_BYTES=40*1024*1024


def save(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    temp.replace(path)
    # Directory fsync is a POSIX durability ritual (and POSIX-only: Windows
    # refuses to open directories). NTFS journals the rename, so nothing to do.
    if os.name!='nt':
        fd=os.open(str(path.parent),os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def submit(payload,key):
    req=urllib.request.Request(URL,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=240) as r:
        return json.load(r)


def download(url):
    if not isinstance(url,str) or not url.startswith('https://'):raise ValueError('HTTPS artifact required')
    with urllib.request.urlopen(url,timeout=90) as r:return r.read(MAX_BYTES+1)


def payload_for(task):
    # Reviewed keyframes are complete static compositions. Dynamic continuity and
    # state ledgers stay in the receipt for board review, never in image prompts.
    if task.get('prompt_source') == 'keyframe_prompts.' + task['role']:
        sections=[task['prompt']]
    else:
        # Kept only for diagnosis of historical parameter-rejected submissions.
        spec={k:v for k,v in task.get('shared_visual_spec',{}).items() if k not in ('text','evidence_boundary','consistency_gate')}
        bible=task.get('continuity_bible','')
        sections=[task['prompt'], 'Role: '+task['role'], 'State: '+json.dumps(task['expected_state'],ensure_ascii=False,separators=(',',':'))]
        if spec:sections.append('Shared: '+json.dumps(spec,ensure_ascii=False,separators=(',',':')))
        if bible and bible not in [str(v) for v in spec.values()]:sections.append('Continuity: '+bible)
    sections.append('No text, numbers, labels or watermark; one full composition.')
    prompt='\n'.join(sections)
    if len(prompt)>1499:raise ValueError('provider_prompt_exceeds_1499_after_lossless_dedup: '+str(len(prompt)))
    return {'model':'image-01','prompt':prompt,'n':1,'aspect_ratio':task['aspect_ratio'],'response_format':'url','prompt_optimizer':False}


class ExternalImages:
    def __init__(self,jobs,output,review_map,config=None,transport=None,fetch=None):
        self.jobs=ImageJobs(jobs);self.out=Path(output).resolve();self.out.mkdir(parents=True,exist_ok=True)
        self.reviews=review_map;self.transport=transport or submit;self.fetch=fetch or download
        self.key=credential(json.loads(Path(config).read_text(encoding="utf-8"))) if config else ''
        if transport is None and not self.key:raise ValueError('Credential configuration required')

    def update(self,task,**changes):
        with (self.jobs.folder/'jobs.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);p=self.jobs.folder/(task['id']+'.json');current=json.loads(p.read_text(encoding="utf-8"))
            if current.get('claim_token')!=task['claim_token']:raise ValueError('Claim ownership changed')
            current.update(changes);save(p,current);return current

    def quarantine(self,task,reason,state='isolated'):
        return self.update(task,state=state,external_image_reason=reason,approval=False)

    def check(self,task):
        if sha256(task['manifest_path'])!=task['manifest_sha256']:raise ValueError('Manifest SHA changed')
        review=self.reviews.get(task['manifest_sha256'])
        if not review:raise ValueError('Missing SHA-bound narrative review mapping')
        require_review(task['manifest_path'],review)
        if task.get('reference_images') or task.get('start_reference'):
            raise ValueError('unsupported_reference: image-01 character reference cannot preserve arbitrary scene/object/start-frame')
        plan=json.loads(Path(task['manifest_path']).read_text(encoding="utf-8"));shot=next(s for s in plan['shots'] if s['id']==task['shot_id'])
        # Explicit references must not disappear even when ImageJobs continuity claim omitted them.
        if shot.get('reference_images') or shot.get('previsual_path'):
            raise ValueError('unsupported_reference: source shot contains reference image')
        if task['aspect_ratio'] not in {'1:1','16:9','9:16','4:3','3:4','3:2','2:3','21:9'}:
            raise ValueError('Unsupported provider aspect ratio')
        expected_prompt=shot.get('keyframe_prompts',{}).get(task['role'])
        explicit_style=plan.get('visual_style_policy')
        style_text=('显式视觉风格：'+json.dumps(explicit_style,ensure_ascii=False)) if explicit_style else PHOTOREAL_POLICY
        if not expected_prompt or task.get('prompt_source')!='keyframe_prompts.'+task['role'] or task['prompt']!=expected_prompt+'。'+style_text:
            raise ValueError('Explicit reviewed role-specific keyframe prompt required; whole-action text forbidden')
        strategy=plan.get('image_generation_strategy')
        if strategy in ('independent_text_keyframes_reviewed_as_board','first_frame_only_visual_only_v1'):
            if task.get('image_generation_strategy')!=strategy or not isinstance(plan.get('shared_visual_spec'),dict) or not plan['shared_visual_spec']:
                raise ValueError('New explicit strategy/shared spec task required')
            if task.get('shared_visual_spec')!=plan['shared_visual_spec'] or task.get('image_conditioning_applied') is not False:
                raise ValueError('Shared specification/conditioning declaration mismatch')
        if strategy=='first_frame_only_visual_only_v1':
            from .first_frame import validate_policy
            validate_policy(plan)
            if task['role']!='start':raise ValueError('first-frame image jobs cannot request endpoint images')
        return review

    def execute(self,task):
        folder=self.out/(task['id']+('_r'+str(task.get('repair_attempt',0)))+('_paramr1' if task.get('external_parameter_repair') else '')+('_techr1' if task.get('external_technical_repair') else ''))
        folder.mkdir(exist_ok=True)
        with (folder/'attempt.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            self.update(task,external_executor='minimax_image01_v1',external_attempt=str(folder))
            intent=folder/'intent.json';response=folder/'response.json';receipt=folder/'receipt.json'
            try:review=self.check(task)
            except Exception as exc:
                result={'state':'isolated','reason':str(exc),'production_approved':False}
                save(receipt,result);self.quarantine(task,result['reason']);return result
            if receipt.exists():
                old=json.loads(receipt.read_text(encoding="utf-8"))
                if old.get('image_path') and sha256(old['image_path'])==old.get('image_sha256'):
                    finished=self.jobs.complete(task['id'],task['claim_token'],old['image_path'])
                    return {**old,'image_job_state':finished['state']}
                return old
            try:payload=payload_for(task)
            except ValueError as exc:
                result={'state':'isolated','reason':str(exc),'production_approved':False};save(receipt,result);self.quarantine(task,result['reason']);return result
            if intent.exists() and not response.exists():
                self.quarantine(task,'submission_unknown_do_not_resubmit',state='external_submission_unknown')
                return {'state':'submission_unknown_do_not_resubmit','attempt':str(folder)}
            if not intent.exists():
                save(intent,{'state':'submission_intent','task_id':task['id'],'claim_token':task['claim_token'],'manifest_path':task['manifest_path'],'manifest_sha256':task['manifest_sha256'],'review_path':str(Path(review).resolve()),'review_sha256':sha256(review),'payload':payload,'payload_sha256':hashlib.sha256(json.dumps(payload).encode()).hexdigest(),'submitted_at':time.time(),'technical_repair':task.get('technical_repair_evidence'),'technical_repair_count':task.get('external_technical_repair',0),'parameter_repair':task.get('parameter_repair_evidence'),'repair_count':task.get('external_parameter_repair',0),'max_submissions':1,'image_generation_strategy':task.get('image_generation_strategy'),'shared_visual_spec':task.get('shared_visual_spec'),'image_conditioning_applied':False,'continuity_validation':'whole_board_review_required','production_approved':False})
                try:
                    data=self.transport(payload,self.key)
                    # Provider payload can echo keys. No credentials ever reach disk.
                    encoded=json.dumps(data,ensure_ascii=False)
                    if self.key:encoded=encoded.replace(self.key,'[REDACTED]')
                    save(response,json.loads(encoded))
                except urllib.error.HTTPError as exc:
                    state='http_rejected' if 400<=exc.code<500 else 'submission_unknown_do_not_resubmit'
                    result={'state':state,'http_status':exc.code,'production_approved':False};save(receipt,result)
                    self.quarantine(task,state,state='isolated' if state=='http_rejected' else 'external_submission_unknown');return result
                except Exception as exc:
                    result={'state':'submission_unknown_do_not_resubmit','error_type':type(exc).__name__,'production_approved':False};save(receipt,result)
                    self.quarantine(task,result['state'],state='external_submission_unknown');return result
            data=json.loads(response.read_text(encoding="utf-8"));items=data.get('data',{});status=data.get('base_resp',{}).get('status_code',0)
            try:
                if status!=0:raise ValueError('Provider returned failure status '+str(status))
                if not isinstance(items,dict):raise ValueError('Unexpected MiniMax response schema')
                b64=items.get('image_base64',[]);urls=items.get('image_urls',[])
                raw=base64.b64decode(b64[0],validate=True) if b64 else self.fetch(urls[0]) if urls else None
                if not raw or len(raw)>MAX_BYTES:raise ValueError('Missing or oversized image')
                with Image.open(io.BytesIO(raw)) as im:dimensions=list(im.size);fmt=im.format;im.verify()
                if fmt not in ('JPEG','PNG','WEBP'):raise ValueError('Unsupported actual image format')
                image=folder/('candidate.'+{'JPEG':'jpg','PNG':'png','WEBP':'webp'}[fmt]);image.write_bytes(raw)
                result={'state':'image_received','image_path':str(image),'image_sha256':sha256(image),'dimensions':dimensions,'provider':'minimax','model':'image-01','response_sha256':sha256(response),'intent_sha256':sha256(intent),'production_approved':False}
                save(receipt,result)
                # complete rechecks manifest SHA, real decode, exact ratio and claim token.
                completed=self.jobs.complete(task['id'],task['claim_token'],image)
                result['image_job_state']=completed['state'];save(receipt,result);return result
            except Exception as exc:
                result={'state':'artifact_or_completion_failed','error_type':type(exc).__name__,'production_approved':False,'response_retained':str(response)}
                save(receipt,result);self.quarantine(task,result['state']);return result

    def repair_explicit_system_failures(self,product=None):
        """One retry only for a persisted 1033 response explicitly containing no data."""
        repaired=[]
        for path in self.jobs.folder.glob('*.json'):
            task=json.loads(path.read_text(encoding="utf-8"))
            if (task.get('external_executor')!='minimax_image01_v1' or task['state']!='isolated' or task.get('external_technical_repair') or task['manifest_sha256'] not in self.reviews or (product and task['product_id']!=product)):continue
            previous=Path(task['external_attempt']);response=previous/'response.json'
            if not response.exists():continue
            data=json.loads(response.read_text(encoding="utf-8"))
            if data.get('base_resp',{}).get('status_code')!=1033 or data.get('data') is not None:continue
            self.check(task);payload_for(task)
            evidence={'reason':'explicit_1033_system_error_data_null','previous_response_path':str(response),'previous_response_sha256':sha256(response),'repair_count':1,'scope':'one unchanged-payload technical retry; no automatic loop'}
            save(previous/'technical_repair_authorization.json',evidence)
            self.update(task,state='claimed',external_technical_repair=1,technical_repair_evidence=evidence)
            repaired.append(task['id'])
        return repaired

    def repair_parameter_rejections(self,product=None):
        repaired=[]
        for path in self.jobs.folder.glob('*.json'):
            task=json.loads(path.read_text(encoding="utf-8"))
            if (task.get('external_executor')!='minimax_image01_v1' or task['state']!='isolated' or task.get('external_parameter_repair') or task['manifest_sha256'] not in self.reviews or (product and task['product_id']!=product)):continue
            previous=Path(task['external_attempt']);response=previous/'response.json';intent=previous/'intent.json'
            if not response.exists() or not intent.exists():continue
            data=json.loads(response.read_text(encoding="utf-8"))
            if data.get('base_resp',{}).get('status_code')!=2013 or data.get('data') is not None:continue
            self.check(task)
            try:new=payload_for(task)
            except ValueError:continue
            old=json.loads(intent.read_text(encoding="utf-8"))
            evidence={'reason':'explicit_2013_invalid_prompt_length_no_image_returned','previous_response_path':str(response),'previous_response_sha256':sha256(response),'previous_intent_path':str(intent),'previous_prompt_sha256':hashlib.sha256(old['payload']['prompt'].encode()).hexdigest(),'new_prompt_sha256':hashlib.sha256(new['prompt'].encode()).hexdigest(),'repair_count':1,'transformation':'deduplicate identical scene specification; exclude text/evidence/review governance metadata; preserve visual specification and target state'}
            save(previous/'parameter_repair_authorization.json',evidence)
            self.update(task,state='claimed',external_parameter_repair=1,parameter_repair_evidence=evidence)
            repaired.append(task['id'])
        return repaired

    def run(self,limit=24,workers=2,product=None,shot_ids=None):
        if not 1<=workers<=2 or limit<1:raise ValueError('Workers 1..2 and positive limit required')
        results=[]
        # One batch dispatcher; separate per-attempt locks protect recovery.
        with (self.out/'dispatcher.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            recovery=[]
            for p in self.jobs.folder.glob('*.json'):
                t=json.loads(p.read_text(encoding="utf-8"))
                if t.get('external_executor')=='minimax_image01_v1' and t['state']=='claimed' and t['manifest_sha256'] in self.reviews and (not product or t['product_id']==product) and (shot_ids is None or t['shot_id'] in shot_ids):recovery.append(t)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                while len(results)<limit:
                    batch=[]
                    for _ in range(min(workers,limit-len(results))):
                        task=recovery.pop(0) if recovery else self.jobs.claim(product,owner='minimax_image01_v1',manifest_shas=set(self.reviews),shot_ids=shot_ids)
                        if not task:break
                        batch.append(pool.submit(self.execute,task))
                    if not batch:break
                    for f in as_completed(batch):
                        try:results.append(f.result())
                        except Exception as exc:results.append({'state':'executor_exception','error_type':type(exc).__name__})
            save(self.out/'batch_summary.json',{'results':results,'count':len(results),'production_approved':False})
        return results
