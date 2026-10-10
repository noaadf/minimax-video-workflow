"""Durable bridge for built-in image tools; never represents request JSON as media.

Imagegen bridges MUST read every claim's aspect_ratio and target_dimensions and
carry them into generation instructions/options; never assume landscape from a
reference image or tool default. Dimensions describe the intended canvas, not a
claim that returned media already meets it. Legacy manifests without a canvas
retain the pipeline's 9:16 / 1080x1920 default. Existing task IDs stay unchanged;
canvas metadata is derived from the SHA-verified manifest when claimed.
New image_job_schema_version=2 tasks enforce the manifest's aspect ratio with
at most one pixel of integer canvas quantization (no arbitrary ratio tolerance)
on complete. Wrong-ratio files are preserved as diagnosed failures, never cropped
or marked completed. Existing versionless tasks keep their historical behavior.
"""
import json,time,uuid,fcntl
from math import gcd
from pathlib import Path
from PIL import Image
from .core.io import sha256, digest, write_json
PHOTOREAL_POLICY='纪实实拍质感，自然中性光与准确白平衡，真实纸张/桌面/肤色/材料，正常对比、自然低饱和；禁止电影调色、橙蓝色偏、HDR、发光特效、塑料感和过度锐化。图解必须标明为示意并清晰可读。'

def _canvas(plan):
 ratio=plan.get('aspect_ratio','9:16')
 try:
  parts=ratio.split(':')
  if len(parts)!=2:raise ValueError()
  rw,rh=map(int,parts)
  if rw<=0 or rh<=0:raise ValueError()
 except (AttributeError,TypeError,ValueError):raise ValueError('Invalid manifest aspect_ratio')
 divisor=gcd(rw,rh);rw//=divisor;rh//=divisor
 dimensions=plan.get('target_dimensions')
 if dimensions is None and ('width' in plan or 'height' in plan):
  dimensions={'width':plan.get('width'),'height':plan.get('height')}
 if dimensions is not None:
  if not isinstance(dimensions,dict):raise ValueError('Invalid manifest target_dimensions')
  w,h=dimensions.get('width'),dimensions.get('height')
  if type(w) is not int or type(h) is not int or min(w,h)<=0 or w*rh!=h*rw:
   raise ValueError('Manifest dimensions disagree with aspect_ratio')
 else:
  # Preserve the production 1080x1920 canvas and support other authored ratios
  # without silently forcing them into a landscape or portrait template.
  scale=max(1,1920//max(rw,rh));w,h=rw*scale,rh*scale
 return {'aspect_ratio':f'{rw}:{rh}','target_dimensions':{'width':w,'height':h}}

def _canvas_check(canvas,dimensions):
 rw,rh=map(int,canvas['aspect_ratio'].split(':'))
 error=abs(dimensions['width']-dimensions['height']*rw/rh) if rh>=rw else abs(dimensions['height']-dimensions['width']*rh/rw)
 return {'status':'passed' if error<=1 else 'failed','expected_aspect_ratio':canvas['aspect_ratio'],
   'target_dimensions':canvas['target_dimensions'],'actual_dimensions':dimensions,
   'rule':'pixel_quantized_aspect_ratio_v2','quantization_error_pixels':error,'maximum_quantization_error_pixels':1}

class ImageJobs:
 def __init__(self,folder):
  self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
 def add(self,manifest,product):
  manifest=Path(manifest).resolve();plan=json.loads(manifest.read_text(encoding="utf-8"));checksum=sha256(manifest)
  for shot in plan['shots']:
   if shot.get('source_path') or shot.get('production_mode')=='deterministic_graphic':continue
   refs=shot.get('reference_images',[])
   from .first_frame import reference_roles
   roles=reference_roles(plan,shot)
   for index,role in enumerate(roles):
    if len(refs)>index and Path(refs[index]).is_file():continue
    taskid=digest([product,checksum,shot['id'],role]);path=self.folder/(taskid+'.json')
    with (self.folder/'jobs.lock').open('a') as lock:
     fcntl.flock(lock,fcntl.LOCK_EX)
     if path.exists():continue
     explicit_style=plan.get('visual_style_policy')
     style_text=('显式视觉风格：'+json.dumps(explicit_style,ensure_ascii=False)) if explicit_style else PHOTOREAL_POLICY
     style_name=explicit_style.get('version','manifest_explicit_style') if isinstance(explicit_style,dict) else 'manifest_explicit_style' if explicit_style else 'documentary_natural_v1'
     keyframes=shot.get('keyframe_prompts',{})
     frame_prompt=keyframes.get(role) if isinstance(keyframes,dict) else None
     if frame_prompt is not None and (not isinstance(frame_prompt,str) or not frame_prompt.strip()):raise ValueError('keyframe_prompts values must be nonempty text')
     if plan.get('image_generation_strategy')=='independent_text_keyframes_reviewed_as_board' and not frame_prompt:
      raise ValueError('New independent image task requires authored keyframe_prompts.'+role)
     write_json(path,{'image_job_schema_version':2,'image_generation_strategy':plan.get('image_generation_strategy'),'image_generation_policy':style_name,'id':taskid,'product_id':product,'manifest_path':str(manifest),'manifest_sha256':checksum,'shot_id':shot['id'],'role':role,'prompt':(frame_prompt or shot['prompt'])+'。'+style_text,'prompt_source':'keyframe_prompts.'+role if frame_prompt else 'shot.prompt','expected_state':shot['storyboard']['state_before' if role=='start' else 'state_after'],'start_reference':refs[0] if refs else None,'state':'pending','created_at':time.time()})
 def _start_references(self,t,plan,shot,tasks):
  prior,same,prior_ends=self._prior_references(t,plan,shot,tasks)
  if prior_ends and any(r['state']!='completed' for r in prior_ends):return None
  if prior and not same:return None
  if prior_ends:same=prior_ends
  return self._anchor_references(t,tasks,same)

 def _end_references(self,t,tasks):
  start=t.get('start_reference')
  if not start:
   first=next((r for _,r in tasks if r['manifest_sha256']==t['manifest_sha256'] and r['shot_id']==t['shot_id'] and r['role']=='start' and r['state']=='completed'),None)
   if not first:return None
   start=first['image_path']
   if sha256(start)!=first['image_sha256']:raise ValueError('Start reference changed')
  if not Path(start).is_file():return None
  refs=[str(Path(start).resolve())]
  return refs

 def _prior_references(self,t,plan,shot,tasks):
  prior=[s['id'] for s in plan['shots'][:plan['shots'].index(shot)] if s['storyboard'].get('scene_id')==t['scene_id']]
  same=[r for _,r in tasks if r['manifest_sha256']==t['manifest_sha256'] and r['shot_id'] in prior and r['state']=='completed']
  prior_ends=self._prior_ends(t,tasks,prior)
  return prior,same,prior_ends

 def _prior_ends(self,t,tasks,prior):
  return [r for _,r in tasks if r['manifest_sha256']==t['manifest_sha256'] and prior and r['shot_id']==prior[-1] and r['role']=='end']

 def _anchor_references(self,t,tasks,same):
  refs=[]
  anchors=same or [r for _,r in tasks if r['manifest_sha256']==t['manifest_sha256'] and r['state']=='completed']
  if anchors:
   anchor=anchors[-1]
   if sha256(anchor['image_path'])!=anchor['image_sha256']:raise ValueError('Continuity reference changed')
   refs=[anchor['image_path']]
   t['reference_role']='same scene continuity' if same else 'environment and materials only; use requested camera and state'
  return refs

 @staticmethod
 def _skip_claim(t,product,manifest_shas,shot_ids):
  return t['state']!='pending' or (product and t['product_id']!=product) or (manifest_shas is not None and t['manifest_sha256'] not in manifest_shas) or (shot_ids is not None and t['shot_id'] not in shot_ids)

 def _claim_task(self,path,t,tasks,owner):
  if sha256(t['manifest_path'])!=t['manifest_sha256']:
   t.update(state='isolated',reason='manifest_changed');write_json(path,t);return None
  plan=json.loads(Path(t['manifest_path']).read_text(encoding="utf-8"));shot=next(s for s in plan['shots'] if s['id']==t['shot_id'])
  try:t.update(_canvas(plan))
  except ValueError as exc:
   t.update(state='isolated',reason='invalid_manifest_canvas',error=str(exc));write_json(path,t);return None
  t['continuity_bible']=plan.get('continuity_bible','')
  t['scene_id']=shot['storyboard'].get('scene_id')
  strategy=plan.get('image_generation_strategy')
  if strategy in ('independent_text_keyframes_reviewed_as_board','first_frame_only_visual_only_v1') and t.get('image_generation_strategy')==strategy:
   return self._claim_independent(path,t,plan,shot,strategy,owner)
  refs=self._claim_references(t,plan,shot,tasks)
  if refs is None:return None
  t.update(state='claimed',claim_token=uuid.uuid4().hex,claimed_at=time.time(),reference_images=refs)
  if owner:t['external_executor']=owner
  write_json(path,t);return t

 def _claim_references(self,t,plan,shot,tasks):
  refs=[]
  if t['role']=='start':
   refs=self._start_references(t,plan,shot,tasks)
  if t['role']=='end':
   refs=self._end_references(t,tasks)
  return refs

 def _claim_independent(self,path,t,plan,shot,strategy,owner):
  # Only newly authored, SHA-bound tasks opt in. Never strip legacy references.
  spec=plan.get('shared_visual_spec')
  if strategy=='first_frame_only_visual_only_v1':
   from .first_frame import validate_policy
   validate_policy(plan)
   if t['role']!='start':raise ValueError('first-frame image task cannot request an end')
  if not isinstance(spec,dict) or not spec or shot.get('reference_images') or shot.get('previsual_path') or t.get('start_reference'):
   t.update(state='isolated',reason='independent_strategy_requires_shared_spec_and_no_authored_references');write_json(path,t);return None
  t.update(state='claimed',claim_token=uuid.uuid4().hex,claimed_at=time.time(),reference_images=[],
    shared_visual_spec=spec,image_conditioning_applied=False,continuity_validation='whole_board_review_required')
  if owner:t['external_executor']=owner
  write_json(path,t);return t

 def claim(self,product=None,owner=None,manifest_shas=None,shot_ids=None):
  with (self.folder/'jobs.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX)
   tasks=[(p,json.loads(p.read_text(encoding="utf-8"))) for p in sorted(self.folder.glob('*.json'))]
   completed_products={r['product_id'] for _,r in tasks if r['state']=='completed'}
   tasks.sort(key=lambda pair:(pair[1]['product_id'] not in completed_products,pair[1]['product_id'],pair[1]['shot_id'],pair[1]['role']=='start'))
   for path,t in tasks:
    if self._skip_claim(t,product,manifest_shas,shot_ids):continue
    claimed=self._claim_task(path,t,tasks,owner)
    if claimed is not None:return claimed
  return None
 def complete(self,taskid,token,image_path):
  with (self.folder/'jobs.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX);path=self.folder/(taskid+'.json');t=json.loads(path.read_text(encoding="utf-8"))
   if t.get('claim_token')!=token:raise ValueError('Claim token mismatch')
   if sha256(t['manifest_path'])!=t['manifest_sha256']:raise ValueError('Manifest changed')
   source=Path(image_path).resolve()
   with Image.open(source) as im:
    dimensions={'width':im.width,'height':im.height};im.verify()
   checksum=sha256(source)
   if t['state'] in ('completed','needs_diagnosed_repair','isolated') and t.get('image_sha256'):
    if t['image_sha256']!=checksum:raise ValueError('Conflicting result')
    return t
   if t['state']!='claimed':raise ValueError('Task not claimed')
   import shutil
   dest=self.folder/'assets'/(taskid+(('_r'+str(t['repair_attempt'])) if t.get('repair_attempt') else '')+source.suffix);dest.parent.mkdir(exist_ok=True);shutil.copyfile(source,dest)
   t.update(state='completed',image_path=str(dest.resolve()),image_sha256=checksum,finished_at=time.time(),approval='candidate_only')
   if t.get('image_job_schema_version',1)>=2:
    canvas=_canvas(json.loads(Path(t['manifest_path']).read_text(encoding="utf-8")))
    check=_canvas_check(canvas,dimensions)
    t.update(image_dimensions=dimensions,canvas_check=check)
    if check['status']!='passed':
     diagnosis=f"Image canvas {dimensions['width']}x{dimensions['height']} does not match required {canvas['aspect_ratio']}; regenerate full composition without cropping"
     t.update(state='isolated' if t.get('repair_attempt') else 'needs_diagnosed_repair',
       approval=False,diagnosis=diagnosis,rejection_reason=diagnosis)
   write_json(path,t);return t

 def reconcile_canvas_quantization(self,taskid):
  """Undo only a verified v1 quantization false positive; preserve its evidence.

  This is a validator-policy reconciliation, not image regeneration or approval.
  It cannot clear content rejection, real aspect mismatch, or spent repair budget.
  """
  with (self.folder/'jobs.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX);path=self.folder/(taskid+'.json');t=json.loads(path.read_text(encoding="utf-8"))
   if t.get('canvas_policy_reconciliation') and t['state']=='completed':return t
   if (t['state']!='needs_diagnosed_repair' or t.get('repair_attempt',0)!=0 or
       t.get('canvas_check',{}).get('rule')!='exact_aspect_ratio_v1' or
       t.get('canvas_check',{}).get('status')!='failed' or
       not t.get('rejection_reason','').startswith('Image canvas ')):
    raise ValueError('Only unspent legacy canvas quantization failure may be reconciled')
   if sha256(t['manifest_path'])!=t['manifest_sha256'] or sha256(t['image_path'])!=t['image_sha256']:
    raise ValueError('Reconciliation evidence changed')
   with Image.open(t['image_path']) as im:
    dimensions={'width':im.width,'height':im.height};im.verify()
   check=_canvas_check(_canvas(json.loads(Path(t['manifest_path']).read_text(encoding="utf-8"))),dimensions)
   if check['status']!='passed':raise ValueError('Real aspect-ratio failure, not quantization')
   archive=self.folder/'history'/(taskid+'.canvas-policy-v1.json');archive.parent.mkdir(exist_ok=True)
   if archive.exists():raise ValueError('Canvas reconciliation history already exists')
   write_json(archive,t)
   t.update(state='completed',approval='candidate_only',canvas_check=check,
    canvas_policy_reconciliation={'prior_evidence_path':str(archive.resolve()),'prior_evidence_sha256':sha256(archive),
      'policy':'pixel_quantized_aspect_ratio_v2','reason':'integer_pixel_quantization_false_positive','at':time.time(),'generation_repair_consumed':False})
   t.pop('diagnosis',None);t.pop('rejection_reason',None);write_json(path,t);return t

 def repair(self,taskid,diagnosis,instruction):
  if not diagnosis.strip() or not instruction.strip():raise ValueError('Concrete diagnosis and repair instruction required')
  with (self.folder/'jobs.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX);path=self.folder/(taskid+'.json');t=json.loads(path.read_text(encoding="utf-8"))
   if t.get('repair_attempt',0)>=1:raise ValueError('One diagnosed repair exhausted')
   if t['state'] not in ('completed','needs_diagnosed_repair'):raise ValueError('Completed failed candidate required')
   if sha256(t['manifest_path'])!=t['manifest_sha256']:raise ValueError('Manifest changed')
   archive=self.folder/'history'/(taskid+'.initial.json');archive.parent.mkdir(exist_ok=True)
   if archive.exists():raise ValueError('Repair history already exists')
   write_json(archive,t)
   t.update(state='pending',repair_attempt=1,diagnosis=diagnosis,repair_instruction=instruction,prior_result={'path':t['image_path'],'sha256':t['image_sha256']},prompt=t['prompt']+'。诊断修复：'+instruction)
   for key in ('claim_token','claimed_at','finished_at','image_path','image_sha256','image_dimensions','canvas_check','inspection','rejection_reason'):t.pop(key,None)
   write_json(path,t);return t

 def reject(self,taskid,reason):
  with (self.folder/'jobs.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX);path=self.folder/(taskid+'.json');t=json.loads(path.read_text(encoding="utf-8"))
   if t['state']!='completed':raise ValueError('Completed candidate required')
   t.update(state='isolated' if t.get('repair_attempt') else 'needs_diagnosed_repair',rejection_reason=reason,approval=False)
   write_json(path,t);return t
