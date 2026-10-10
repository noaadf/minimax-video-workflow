import base64,io,json
from pathlib import Path
from PIL import Image
import pytest
from workflow.external_image_executor import ExternalImages,save
from workflow.storyboard_image_jobs import ImageJobs
from workflow.plan import sha256


def setup(tmp,monkeypatch,count=1,refs=False):
    monkeypatch.setattr('workflow.external_image_executor.require_review',lambda m,r:None)
    plan={'aspect_ratio':'9:16','shots':[{'id':f'S{i}','prompt':'gray mineral','keyframe_prompts':{'start':'gray mineral resting','end':'gray mineral moved'},'storyboard':{'scene_id':f'SC{i}','state_before':{},'state_after':{}}} for i in range(count)]}
    if refs:plan['shots'][0]['reference_images']=['reference.png']
    p=tmp/'manifest.json';p.write_text(json.dumps(plan));review=tmp/'review.json';review.write_text('{}');jobs=ImageJobs(tmp/'jobs');jobs.add(p,'product')
    im=Image.new('RGB',(90,160),'gray');buf=io.BytesIO();im.save(buf,format='PNG');data={'base_resp':{'status_code':0},'data':{'image_base64':[base64.b64encode(buf.getvalue()).decode()]}}
    calls=[]
    def transport(payload,key):calls.append(payload);return data
    e=ExternalImages(tmp/'jobs',tmp/'out',{sha256(p):str(review)},transport=transport)
    return e,calls,p


def test_real_image_complete_and_restart_no_submit(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch);r=e.run();assert r[0]['image_job_state']=='completed';assert len(calls)==1
    assert e.run()==[] and len(calls)==1
    t=json.loads(next((tmp_path/'jobs').glob('*.json')).read_text());assert t['approval']=='candidate_only';assert Path(t['image_path']).is_file()


def test_unknown_timeout_never_retried(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch)
    def timeout(*a):calls.append(1);raise TimeoutError()
    e.transport=timeout;assert e.run()[0]['state']=='submission_unknown_do_not_resubmit';e.run();assert len(calls)==1


def test_recover_crash_after_intent(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch);t=e.jobs.claim();folder=e.out/(t['id']+'_r0');folder.mkdir();save(folder/'intent.json',{'state':'submission_intent'})
    e.update(t,external_executor='minimax_image01_v1',external_attempt=str(folder));assert e.run()[0]['state']=='submission_unknown_do_not_resubmit';assert calls==[]


def test_recover_candidate_before_complete(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch);real_complete=e.jobs.complete
    def crash(*a):raise KeyboardInterrupt()
    e.jobs.complete=crash;t=e.jobs.claim()
    with pytest.raises(KeyboardInterrupt):e.execute(t)
    e.jobs.complete=real_complete;assert e.run()[0]['image_job_state']=='completed';assert len(calls)==1


def test_unsupported_reference_no_api(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch,refs=True);r=e.run();assert r[0]['state']=='isolated' and 'unsupported_reference' in r[0]['reason'];assert calls==[]


def test_failure_does_not_block_another_product(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch);plan=json.loads(p.read_text());q=tmp_path/'second.json';q.write_text(json.dumps(plan));e.jobs.add(q,'product2');e.reviews[sha256(q)]=e.reviews[sha256(p)]
    transport=e.transport
    def sometimes(payload,key):
        if not calls:calls.append('failed');raise ValueError()
        return transport(payload,key)
    e.transport=sometimes;r=e.run(workers=1);assert len(r)==2;assert any(x.get('image_job_state')=='completed' for x in r)


def test_missing_review_never_submits(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch);e.reviews={};assert e.run()==[];assert not calls
    assert json.loads(next(e.jobs.folder.glob('*.json')).read_text())['state']=='pending'


def test_explicit_new_strategy_generates_both_states_no_image_conditioning(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch);plan=json.loads(p.read_text())
    plan.update(image_generation_strategy='independent_text_keyframes_reviewed_as_board',shared_visual_spec={'material':'dull gray ore','camera':'portrait'})
    plan['shots'][0]['storyboard']['state_after']={'grains':'falling'}
    new=tmp_path/'new.json';new.write_text(json.dumps(plan));e.reviews[sha256(new)]=next(iter(e.reviews.values()));e.jobs.add(new,'newproduct')
    r=e.run(product='newproduct');assert len(r)==2 and all(x['image_job_state']=='completed' for x in r)
    assert len(calls)==2 and all('subject_reference' not in p for p in calls)
    intents=list(e.out.glob('*/intent.json'));assert all(json.loads(p.read_text())['image_conditioning_applied'] is False for p in intents)


def test_strategy_cannot_strip_explicit_reference(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch);plan=json.loads(p.read_text());plan.update(image_generation_strategy='independent_text_keyframes_reviewed_as_board',shared_visual_spec={'material':'ore'});plan['shots'][0]['reference_images']=['missing.png']
    new=tmp_path/'new.json';new.write_text(json.dumps(plan));e.reviews[sha256(new)]=next(iter(e.reviews.values()));e.jobs.add(new,'newproduct')
    assert e.run(product='newproduct')==[];assert calls==[]
    tasks=[json.loads(p.read_text()) for p in e.jobs.folder.glob('*.json')];assert any(t['product_id']=='newproduct' and t['state']=='isolated' for t in tasks)


def test_claim_owner_persisted_before_execute(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch);t=e.jobs.claim(owner='minimax_image01_v1');assert t['external_executor']=='minimax_image01_v1'
    # Simulated stop immediately after claim: next run recovers safely before submit.
    assert e.run()[0]['image_job_state']=='completed';assert len(calls)==1


def test_new_explicit_style_not_overridden_and_old_tasks_immutable(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch);old={p.name:p.read_text() for p in e.jobs.folder.glob('*.json')}
    plan=json.loads(p.read_text());plan['visual_style_policy']={'version':'paper_cut_v1','generated_scenes':'tactile paper-cut illustration, muted natural pigments'}
    new=tmp_path/'styled.json';new.write_text(json.dumps(plan));e.jobs.add(new,'styled')
    rows=[json.loads(p.read_text()) for p in e.jobs.folder.glob('*.json')];styled=next(t for t in rows if t['product_id']=='styled')
    assert 'paper-cut illustration' in styled['prompt'] and '纪实实拍质感' not in styled['prompt']
    assert all((e.jobs.folder/name).read_text()==data for name,data in old.items())


def test_explicit_2013_one_parameter_repair_and_preserved_receipt(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch);real=e.transport
    def reject(payload,key):calls.append(payload);return {'base_resp':{'status_code':2013},'data':None}
    e.transport=reject;assert e.run()[0]['state']=='artifact_or_completion_failed'
    first=list(e.out.glob('*/response.json'))[0];before=first.read_bytes()
    assert len(e.repair_parameter_rejections())==1;e.transport=real
    assert e.run()[0]['image_job_state']=='completed' and len(calls)==2
    assert first.read_bytes()==before and e.repair_parameter_rejections()==[]
    intent=json.loads(next(e.out.glob('*_paramr1/intent.json')).read_text());assert intent['repair_count']==1


def test_semantically_overlong_prompt_isolated_without_submit(tmp_path,monkeypatch):
    from workflow.external_image_executor import payload_for
    e,calls,p=setup(tmp_path,monkeypatch);task=e.jobs.claim();task['prompt']='矿'*1500
    with pytest.raises(ValueError,match='1499'):payload_for(task)
    assert calls==[]


def test_authored_role_specific_prompts_on_new_sha_only(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch);plan=json.loads(p.read_text());plan['shots'][0]['storyboard']['state_after']={'hand':'absent'};plan['shots'][0]['keyframe_prompts']={'start':'One untouched specimen, no hand visible','end':'One restored specimen, no hand visible'}
    new=tmp_path/'frames.json';new.write_text(json.dumps(plan));e.jobs.add(new,'new')
    tasks=[json.loads(p.read_text()) for p in e.jobs.folder.glob('*.json')];tasks=[t for t in tasks if t['product_id']=='new']
    assert len(tasks)==2
    for t in tasks:assert t['prompt'].startswith(plan['shots'][0]['keyframe_prompts'][t['role']]) and t['prompt_source']=='keyframe_prompts.'+t['role']


def test_static_prompt_never_reinjects_dynamic_global_context(tmp_path,monkeypatch):
    from workflow.external_image_executor import payload_for
    e,calls,p=setup(tmp_path,monkeypatch);task=e.jobs.claim()
    task.update(continuity_bible='A hand enters and exits; reveal full cat face',shared_visual_spec={'scene_identity':'A hand enters and exits; reveal full cat face'},expected_state={'global':'full cat face'})
    prompt=payload_for(task)['prompt']
    assert task['prompt'] in prompt
    assert 'hand enters' not in prompt and 'full cat face' not in prompt


def test_unmapped_claim_recovery_is_untouched(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch);t=e.jobs.claim(owner='minimax_image01_v1')
    e.reviews={}
    assert e.run()==[] and not calls
    assert json.loads((e.jobs.folder/(t['id']+'.json')).read_text())['state']=='claimed'


def test_explicit_1033_one_technical_retry(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch)
    def reject(payload,key):calls.append(payload);return {'base_resp':{'status_code':1033},'data':None}
    e.transport=reject;e.run()
    original=next(e.out.glob('*/response.json'));old=original.read_bytes()
    assert len(e.repair_explicit_system_failures())==1
    e.run()
    assert len(calls)==2 and e.repair_explicit_system_failures()==[]
    assert original.read_bytes()==old


def test_first_frame_strategy_only_one_start_per_shot(tmp_path,monkeypatch):
    from workflow.first_frame import CONTRACT, STRATEGY
    e,calls,p=setup(tmp_path,monkeypatch)
    plan=json.loads(p.read_text());plan.update(image_generation_strategy=STRATEGY,shared_visual_spec={'scene':'grey specimen'},pacing_policy='storyboard_v1',review_mode='api',use_native_foley=False,audio_policy={'source_audio_enabled':False})
    plan['shots'][0]['generation_contract']=CONTRACT
    plan['shots'][0]['keyframe_prompts'].pop('end')
    new=tmp_path/'first.json';new.write_text(json.dumps(plan));e.reviews[sha256(new)]=next(iter(e.reviews.values()));e.jobs.add(new,'firstproduct')
    result=e.run(product='firstproduct')
    assert len(result)==1 and result[0]['image_job_state']=='completed' and len(calls)==1


def test_shot_filter_does_not_claim_or_recover_unselected(tmp_path,monkeypatch):
    e,calls,p=setup(tmp_path,monkeypatch)
    assert e.run(shot_ids={'nonexistent'})==[] and not calls
    task=e.jobs.claim(owner='minimax_image01_v1')
    assert e.run(shot_ids={'nonexistent'})==[] and not calls
    assert json.loads((e.jobs.folder/(task['id']+'.json')).read_text())['state']=='claimed'
    result=e.run(shot_ids={task['shot_id']})
    assert result and calls
