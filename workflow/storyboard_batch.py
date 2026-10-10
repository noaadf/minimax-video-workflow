"""Stage verified preflight evidence without changing authored content or submitting."""

import fcntl
import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from . import media, storyboard
from .core.io import digest, sha256, write_json
from .narrative_gate import require_review, bind_runtime_copy
from .preflight_schema import require_compiled_manifest, validate_manifest
from .storyboard import reuse_preflight

def mint_job_id(product_id, source_sha, taken_ids):
    """P0 (reorg v2): stage mints a collision-proof job id from the reviewed
    content — queue id collisions must never again require authors to hand-bump
    versions (whole-film-v4→v9 was entirely manual). Deterministic from the
    acceptance sha; increments only against ids already in the queue."""
    n = 1
    while True:
        candidate = f'{product_id}-r{n}-{source_sha[:8]}'
        if candidate not in taken_ids:
            return candidate
        n += 1


def resolve_source_identity(plan,parent,resolution):
 if not resolution or resolution.get('candidate_source_packet_digest')!=digest(plan.get('source_packet')) or resolution.get('parent_source_packet_digest')!=digest(parent.get('source_packet')):
  raise ValueError('Candidate source differs from parent; exact identity resolution required')
 if not resolution.get('supplemental_sources'):
  raise ValueError('Identity resolution requires verified supplemental sources')
 result={**plan,'source_packet':parent['source_packet'],'supplemental_sources':resolution['supplemental_sources']}
 from workflow.revised_sources import validate_revised_sources
 validate_revised_sources(result,parent)
 return result

def stage(acceptance_path,product_id,parent_job_id,root,source_identity_resolution=None):
 source=Path(acceptance_path).resolve();a=json.loads(source.read_text(encoding="utf-8"))
 if a.get('status')!='ready_for_generation':raise ValueError('Preflight not passed')
 for key in ('manifest','visual_review','edl','preview'):
  if sha256(a[key+'_path'])!=a[key+'_sha256']:raise ValueError('Changed '+key)
 plan=json.loads(Path(a['manifest_path']).read_text(encoding="utf-8"));require_compiled_manifest(plan)
 edl=json.loads(Path(a['edl_path']).read_text(encoding="utf-8"))
 voice={'effective_narration':plan['narration'],'audio_path':edl.get('narration_path')}
 bed={'audio_path':edl.get('music_path')}
 validate_manifest(plan,'compose',timing='ready',context={'voice':voice,'bed':bed}).require()
 require_review(a['manifest_path'],a['review_path'])
 with closing(sqlite3.connect('file:'+str(Path(root).resolve()/'queue.sqlite')+'?mode=ro',uri=True)) as db:
  row=db.execute('select manifest from jobs where id=?',(parent_job_id,)).fetchone()
 if not row:raise ValueError('Parent job missing')
 parent=json.loads(row[0]); source_packet=parent.get('source_packet')
 if not source_packet:raise ValueError('Parent source packet missing')
 registered_path=Path(root).resolve()/'user_repair30_20261002/status.json'
 registered=json.loads(registered_path.read_text(encoding="utf-8"))['items'] if registered_path.exists() else []
 matches=[r for r in registered if r['product_id']==product_id]
 if len(matches)>1:raise ValueError('Ambiguous original repair request')
 request_id=matches[0]['request_id'] if matches else None
 identity=digest(['source-bound-v3-repair',product_id,parent_job_id,sha256(source),source_packet,request_id] + ([source_identity_resolution] if source_identity_resolution else []))
 folder=Path(root).resolve()/'storyboard_rollout'/'staged'/identity;folder.mkdir(parents=True,exist_ok=True)
 receipt=folder/'staging.json'
 if receipt.exists():
  prior=json.loads(receipt.read_text(encoding="utf-8"))
  for key in ('manifest','review','preflight'):
   if sha256(prior[key+'_path'])!=prior[key+'_sha256']:raise ValueError('Staged evidence changed')
  return prior
 # Copy bytes verbatim: the model-approved manifest and EDL hashes stay unchanged.
 for key in ('manifest','review','visual_review','edl','preview','contact','report'):
  original=Path(a[key+'_path']).resolve();dest=folder/(key+original.suffix)
  if dest.exists() and sha256(dest)!=sha256(original):raise ValueError('Staging collision')
  if not dest.exists():shutil.copyfile(original,dest)
  a[key+'_path']=str(dest)
 acceptance=folder/'acceptance.json';write_json(acceptance,a)
 plan=json.loads(Path(a['manifest_path']).read_text(encoding="utf-8"))
 if plan.get('source_packet') not in (None,source_packet):
  plan=resolve_source_identity(plan,parent,source_identity_resolution)
  write_json(folder/'source_identity_resolution.json',source_identity_resolution)
 plan['source_packet']=source_packet
 from workflow.revised_sources import validate_revised_sources
 validate_revised_sources(plan,parent)
 plan.update(root_job_id=product_id,parent_job_id=parent_job_id,approved_story_preflight={'path':str(acceptance),'sha256':sha256(acceptance)})
 if request_id:plan['user_repair_request_id']=request_id
 with closing(sqlite3.connect('file:'+str(Path(root).resolve()/'queue.sqlite')+'?mode=ro',uri=True)) as db:
     taken={r[0] for r in db.execute('select id from jobs').fetchall()}
 plan['id']=mint_job_id(product_id,sha256(source),taken)
 require_compiled_manifest(plan)
 manifest=folder/'production_manifest.json';write_json(manifest,plan)
 review=manifest.with_suffix('.narrative_review.json');bind_runtime_copy(a['manifest_path'],a['review_path'],manifest,review)
 reuse_preflight(plan,voice,bed,folder)
 result={'source_acceptance_path':str(source),'source_acceptance_sha256':sha256(source),'product_id':product_id,'parent_job_id':parent_job_id}
 for key,path in [('manifest',manifest),('review',review),('preflight',acceptance)]:result.update({key+'_path':str(path),key+'_sha256':sha256(path)})
 write_json(receipt,result);return result



"""Register one immutable, staged request; only the existing owner may admit it."""

def register(packet,production_root):
 root=Path(production_root).resolve();rollout=root/'storyboard_rollout'
 for name in ('manifest','review','preflight'):
  path=Path(packet[name+'_path']).resolve()
  if not path.is_relative_to(rollout) or sha256(path)!=packet[name+'_sha256']:raise ValueError('Unstaged or changed '+name)
 require_review(packet['manifest_path'],packet['review_path'])
 acceptance=json.loads(Path(packet['preflight_path']).read_text(encoding="utf-8"))
 if acceptance.get('status')!='ready_for_generation':raise ValueError('Preflight did not pass')
 plan=json.loads(Path(packet['manifest_path']).read_text(encoding="utf-8"))
 if not plan.get('root_job_id') or not plan.get('parent_job_id'):raise ValueError('Original product identity required')
 identity=digest([plan['root_job_id'],packet['manifest_sha256']]);folder=rollout/'requests'/identity;folder.mkdir(parents=True,exist_ok=True)
 path=folder/'deployment_request.json'
 with (folder/'register.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if path.exists():
   prior=json.loads(path.read_text(encoding="utf-8"))
   if prior['manifest_sha256']!=packet['manifest_sha256']:raise ValueError('Request conflict')
   return {'path':str(path),'new':False,'state':prior['state']}
  write_json(folder/'status.json',{'state':'awaiting_singleton_admission','film_ready':False})
  write_json(path,{**packet,'state':'approved','product_id':plan['root_job_id']})
 return {'path':str(path),'new':True,'state':'approved'}



"""Bounded preflight dispatcher; missing assets and failures cannot block peers.

Fault isolation (owner directive 2026-10-06): transient review-API failures
(timeouts, provider 5xx) get bounded automatic retries with artifact archiving
— they must never masquerade as content verdicts requiring the manual
archive-and-delete ritual. Content failures (real review verdicts, schema
violations) stay isolated as before.
"""
ROOT=Path('output/production/user_repair30_20261002/batch_story_v3')

MAX_TRANSIENT_RETRIES=3


def transient_failure(out: Path, exc=None, *, classification=None) -> bool:
    """Classify the actual error before consulting persisted review evidence.

    Only storyboard's explicit review-refusal wrapper loses the original
    exception; unrelated code/config failures cannot be masked by stale records.
    """
    from .core.failures import Disposition, classify
    from .review_failure import from_record
    failure = classify(exc) if exc is not None else None
    source = 'exception' if failure else None
    review_wrapper = (type(exc) is ValueError and str(exc) ==
                      'keyframe animatic needs revision; see keyframe_review.json')
    if exc is None or review_wrapper:
        for name in ('keyframe_review.json', 'story_review.json'):
            try:
                record = json.loads((out / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            recorded, origin = from_record(record)
            if recorded is not None:
                failure, source = recorded, origin
                break
            # An actual content verdict is authoritative, even if a stale
            # failure exists in another lane. It cannot request a retry.
            if name == 'keyframe_review.json' and record.get('review'):
                break
    if classification is not None and failure is not None:
        classification.update(failure=failure.record(), classification_source=source)
    return failure is not None and failure.disposition == Disposition.RETRY

def archive_reviewable(out: Path, attempt: int) -> None:
 """Move re-render blockers aside so the next pass can run clean.

 roughcut refuses to overwrite a non-empty animatic dir (evidence-freeze guard)
 and stale review records would shadow fresh verdicts, so a retry archives
 exactly those — never the reusable TTS/music artifacts."""
 dest=out/f'retry_archive_{attempt}';dest.mkdir(parents=True,exist_ok=True)
 for name in ('animatic','keyframe_contact.jpg','keyframe_contact.jpg.samples.json',
              'keyframe_review.json','story_review.json'):
  src=out/name
  if src.exists():
   shutil.move(str(src),str(dest/name))

def one(item):
 m=Path(item['manifest_path']);plan=json.loads(m.read_text(encoding="utf-8"));out=m.parent/'preflight';status=out/'dispatch.json';key=sha256(m)
 if status.exists():
  old=json.loads(status.read_text(encoding="utf-8"))
  if old['manifest_sha256']!=key:return {'product_id':item['product_id'],'state':'isolated_changed_manifest'}
  if old.get('state')=='transient_retry':
   attempts=old.get('retry_attempts',0)
   if attempts>=MAX_TRANSIENT_RETRIES:
    old=dict(old,state='isolated_preflight');write_json(status,old);return old
   archive_reviewable(out,attempts+1)  # bounded auto-retry, not the manual ritual
  else:
   return old # Includes interrupted/running states: reconcile evidence, never blind API retry.
 try:
  require_compiled_manifest(plan)
  audio={'narration_path':str((out/'narration.wav').resolve()),'music_path':str((out/'music.wav').resolve())}
  validate_manifest(plan,'speech',timing='before_claim',context=audio).require()
  validate_manifest(plan,'music',timing='before_claim',context=audio).require()
 except ValueError as e:
  return {'product_id':item['product_id'],'state':'isolated_preflight','error':str(e),'error_type':type(e).__name__}
 missing=storyboard.missing_previsuals(plan)
 if missing:return {'product_id':item['product_id'],'state':'awaiting_assets','missing':missing}
 out.mkdir(exist_ok=True)
 prior=json.loads(status.read_text(encoding="utf-8")) if status.exists() else {}
 row={'product_id':item['product_id'],'manifest_sha256':key,'state':'running','pid':os.getpid(),
      'started_at':time.time(),'retry_attempts':prior.get('retry_attempts',0)};write_json(status,row)
 try:
  voice=media.narration(plan,audio['narration_path']);voice['audio_path']=audio['narration_path'];media.music(plan,audio['music_path'])
  bed={'audio_path':audio['music_path']}
  validate_manifest(plan,'compose',timing='ready',context={'voice':voice,'bed':bed}).require()
  result=storyboard.preflight(plan,voice,bed,out)
  write_json(out/'acceptance.json',result);row.update(state='ready_for_staging',acceptance_path=str(out/'acceptance.json'))
 except Exception as e:
  if transient_failure(out, e, classification=row):
   row.update(state='transient_retry',error=str(e),error_type=type(e).__name__,
              retry_attempts=row['retry_attempts']+1)
  else:
   row.update(state='isolated_preflight',error=str(e),error_type=type(e).__name__)
 row['finished_at']=time.time();write_json(status,row);return row
