"""Observable story progression and persistent scene state for new films."""
from pathlib import Path
from .core.io import digest

POLICY = 'storyboard_v1'
PREFLIGHT_VERSION = 'story-preflight-v2-verified-replay'

def enabled(plan):
    return plan.get('pacing_policy') == POLICY

def validate(plan):
    from .first_frame import validate_policy
    validate_policy(plan)
    from .framing import filter_for
    filter_for(1080,1920,plan.get('framing_policy'))
    story = plan.get('story_contract', {})
    _story_fields(story)
    ids = _story_beats(story)
    used, ledger, actions, source_windows, visual_prompts = [], {}, set(), {}, set()
    for shot in plan['shots']:
        _shot_source(shot, source_windows, visual_prompts)
        row = _shot_storyboard(shot, ids)
        if not used or used[-1] != row['beat_id']:
            used.append(row['beat_id'])
        before, after = _shot_states(row)
        scene = _shot_transition(shot, row, before, ledger)
        _shot_action(shot, row, scene, before, after, actions)
        ledger[scene] = after
    if used != ids:
        raise ValueError('shots must realize every causal beat once in chronological order')
    return story


def _story_fields(story):
    for key in ('central_question', 'story_one_sentence', 'protagonist', 'goal', 'obstacle', 'resolution'):
        if not isinstance(story.get(key), str) or not story[key].strip():
            raise ValueError('story_contract requires ' + key)


def _story_beats(story):
    beats = story.get('beats', [])
    if not isinstance(beats, list) or not beats or any(not isinstance(b, dict) for b in beats):
        raise ValueError('story requires causal beats')
    ids = [b.get('id') for b in beats]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('story beats require unique ids')
    _beat_chain(beats)
    return ids


def _beat_chain(beats):
    previous = None
    for beat in beats:
        if beat.get('caused_by') != previous:
            raise ValueError('causal beats must link to the preceding beat')
        for key in ('action', 'result'):
            if not isinstance(beat.get(key), str) or not beat[key].strip():
                raise ValueError('causal beat requires ' + key)
        previous = beat['id']


def _shot_source(shot, source_windows, visual_prompts):
    if shot.get('source_fit_policy') not in (None,'source_window','whole_action'):
        raise ValueError('unknown source_fit_policy')
    if shot.get('source_fit_policy')=='whole_action' and shot.get('source_start',0)!=0:
        raise ValueError('whole_action must retain the complete source from zero')
    if shot.get('source_path'):
        path = str(Path(shot['source_path']).expanduser().resolve())
        start = shot.get('source_start', 0)
        end = start + shot['duration_seconds']
        if any(max(start, a) < min(end, b) - .001 for a, b in source_windows.get(path, [])):
            raise ValueError('repeated source footage window in ' + shot['id'])
        source_windows.setdefault(path, []).append((start, end))
    else:
        if not isinstance(shot.get('prompt'), str) or not shot['prompt'].strip():
            raise ValueError('shot needs an authored visual prompt')
        prompt = ' '.join(shot['prompt'].split()).lower()
        if prompt in visual_prompts:
            raise ValueError('repeated generation prompt in ' + shot['id'])
        visual_prompts.add(prompt)


def _shot_storyboard(shot, ids):
    row = shot.get('storyboard', {})
    for key in ('scene_id', 'purpose', 'attention_target', 'action', 'result', 'cut_reason'):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(shot['id'] + ': storyboard requires ' + key)
    if row.get('beat_id') not in ids:
        raise ValueError('shot references unknown story beat')
    return row


def _shot_states(row):
    before, after = row.get('state_before'), row.get('state_after')
    if not isinstance(before, dict) or not before or not isinstance(after, dict) or set(before) != set(after):
        raise ValueError('shot needs complete before/after state with identical keys')
    return (before, after)


def _shot_transition(shot, row, before, ledger):
    scene = row['scene_id']
    transition = row.get('transition', {})
    kind = transition.get('kind')
    if kind not in ('establish', 'continuous', 'cutaway', 'return', 'ellipsis'):
        raise ValueError('shot needs an explicit transition kind')
    prior = ledger.get(scene)
    if prior is not None:
        if kind in ('establish', 'cutaway'):
            raise ValueError('existing scene must preserve its ledger on return')
        _state_handoff(shot, prior, before, transition, kind)
    elif kind not in ('establish', 'cutaway'):
        raise ValueError('new scene needs establish or cutaway transition')
    return scene


def _state_handoff(shot, prior, before, transition, kind):
    changes = {k for k in set(prior) | set(before) if prior.get(k) != before.get(k)}
    if changes:
        allowed = transition.get('allowed_changes', [])
        if (kind != 'ellipsis' or set(allowed) != changes or
                not transition.get('explanation')):
            raise ValueError('unexplained scene state jump in ' + shot['id'])


def _shot_action(shot, row, scene, before, after, actions):
    if before == after and not row.get('new_information'):
        raise ValueError('unchanged shot must add explicit new information')
    signature = digest([scene, row['action'], row['result'], before, after, row.get('new_information')])
    if signature in actions:
        raise ValueError('repeated action/result; changing camera alone is not a new shot')
    actions.add(signature)

def roughcut_row(s):
    """Shot-asset contract (round-4 refactor): the ONE place that knows how a
    plan shot becomes a roughcut/EDL row. Preflight, reuse verification and any
    future consumer must build rows here — drift between the two former copies
    was invisible until an EDL mismatch surfaced at delivery."""
    row={'id':s['id'],'kind':'video' if s.get('source_path') else 'image',
         'source_path':s.get('source_path') or s['previsual_path'],
         'duration_seconds':s['duration_seconds'],'purpose':s['storyboard']['purpose'],
         'source_start':s.get('source_start',0)}
    if s.get('source_fit_policy'):row['source_fit_policy']=s['source_fit_policy']
    if not s.get('source_path') and len(s.get('reference_images',[]))==2:
        row['end_source_path']=s['reference_images'][1]
    return row


def missing_previsuals(plan, base=Path('.')):
    """Planning may be saved without images; submission must wait for real assets."""
    missing = []
    for shot in plan['shots']:
        path = shot.get('previsual_path')
        if not path or not (base / Path(path).expanduser()).is_file():
            missing.append(shot['id'] + ':previsual_path')
        if shot.get('source_path') and not (base / Path(shot['source_path']).expanduser()).is_file():
            missing.append(shot['id'] + ':source_path')
        if not shot.get('source_path'):
            if shot.get('production_mode') == 'deterministic_graphic':
                missing.append(shot['id'] + ':deterministic source_path (never dispatch a text graphic to a video model)')
                continue
            refs = shot.get('reference_images', [])
            changes = shot['storyboard']['state_before'] != shot['storyboard']['state_after']
            from .first_frame import reference_roles
            required = len(reference_roles(plan, shot))
            if len(refs) != required:
                missing.append(shot['id'] + f':reference_images ({required} start/end frame(s) required)')
            elif any(not (base / Path(ref).expanduser()).is_file() for ref in refs):
                missing.append(shot['id'] + ':missing reference image')
            elif path and (base / Path(path).expanduser()).resolve() != (base / Path(refs[0]).expanduser()).resolve():
                missing.append(shot['id'] + ':previsual must be the generation start reference')
            elif required == 2 and (Path(refs[0]).resolve() == Path(refs[1]).resolve()):
                missing.append(shot['id'] + ':action requires distinct start and end keyframes')
    return missing

def write_keyframe_requests(plan, manifest_path, destination):
    """Persist actionable, manifest-bound asset work without dispatching paid generation."""
    from .core.io import sha256, write_json
    from .first_frame import reference_roles, enabled as first_frame_enabled
    validate(plan)
    requests=[]
    missing=missing_previsuals(plan,Path(manifest_path).parent)
    missing_ids={item.split(':',1)[0] for item in missing}
    for shot in plan['shots']:
        row=shot['storyboard']
        if shot['id'] not in missing_ids:continue
        if shot.get('source_path'):
            requests.append({'shot_id':shot['id'],'kind':'source_previsual','source_path':shot['source_path'],
                'source_start':shot.get('source_start',0),'purpose':row['purpose'],
                'instruction':'Verify the supplied source exists and extract a real frame; do not generate replacement footage.'})
            continue
        requests.append({'shot_id':shot['id'],'kind':'deterministic_graphic' if
            shot.get('production_mode')=='deterministic_graphic' else
            'first_frame_keyframe' if first_frame_enabled(plan) else 'start_end_keyframes',
            'scene_id':row['scene_id'],'purpose':row['purpose'],'action':row['action'],
            'result':row['result'],'state_before':row['state_before'],'state_after':row['state_after'],
            'transition':row['transition'],'visual_prompt':shot['prompt'],
            'reference_count':len(reference_roles(plan,shot)),
            'text_policy':'Postproduction text only; no invented labels, logos, measurements or claims.'})
    data={'schema_version':1,'status':'awaiting_storyboard','manifest_path':str(Path(manifest_path).resolve()),
          'manifest_sha256':sha256(manifest_path),'missing':missing,
          'continuity_bible':plan.get('continuity_bible',{}),'requests':requests,
          'dispatch_authorized':False,'generation_task_submitted':False}
    write_json(destination,data)
    return str(Path(destination).resolve())

def reuse_preflight(plan, voice, bed, folder):
    """Replay a verified planning decision; never reroll a completed model check."""
    import json
    from .core.io import sha256, write_json
    from .plan import freeze_assets
    from .narrative_gate import require_review,authored_fields
    from .roughcut import validate as validate_edl
    binding=plan['approved_story_preflight'];path=Path(binding['path']).resolve()
    if sha256(path)!=binding.get('sha256'):raise ValueError('Approved preflight acceptance changed')
    acceptance=json.loads(path.read_text(encoding="utf-8"))
    if acceptance.get('status')!='ready_for_generation':raise ValueError('Approved preflight did not pass')
    for name in ('manifest','preview','edl','visual_review'):
        if sha256(acceptance[name+'_path'])!=acceptance.get(name+'_sha256'):
            raise ValueError('Approved preflight evidence changed: '+name)
    require_review(acceptance['manifest_path'],acceptance['review_path'])
    approved=json.loads(Path(acceptance['manifest_path']).read_text(encoding="utf-8"))
    # Runtime freezing may normalize a missing reference list to [] or resolve
    # relative assets. Canonicalize both by the same existing freezer, without
    # weakening any authored field comparison.
    a=freeze_assets(approved,Path(acceptance['manifest_path']).parent)
    b=freeze_assets({**plan,'narration':voice['effective_narration']},Path(folder))
    if any(a.get(k)!=b.get(k) for k in authored_fields(a)):
        raise ValueError('Authored story changed after approved preflight')
    visual=json.loads(Path(acceptance['visual_review_path']).read_text(encoding="utf-8"))
    answer=visual.get('review',{})
    if (visual.get('status')!='automatic_review_passed' or visual.get('review_stage')!='preflight'
        or not visual.get('receipt',{}).get('model') or answer.get('verdict')!='pass'
        or any(f.get('severity') in ('medium','high') for f in answer.get('findings',[]))):
        raise ValueError('Completed planning visual review required')
    edl=json.loads(Path(acceptance['edl_path']).read_text(encoding="utf-8"));validate_edl(edl)
    picture=freeze_assets(edl['caption_plan'],Path(acceptance['edl_path']).parent)
    if any(a.get(k)!=picture.get(k) for k in authored_fields(a)):
        raise ValueError('Approved EDL caption story differs from reviewed manifest')
    expected=[roughcut_row(s) for s in approved['shots']]
    if edl['shots']!=expected:raise ValueError('Approved EDL pictures differ from reviewed storyboard')
    for key,result in (('narration_path',voice),('music_path',bed)):
        if sha256(result['audio_path'])!=edl['input_sha256'][edl[key]]:
            raise ValueError('Actual production audio differs from approved preflight')
    result={**acceptance,'reused_approved_preflight':True,'acceptance_path':str(path),
            'acceptance_sha256':sha256(path),'runtime_story_id':plan['id']}
    receipt=Path(folder)/'approved_preflight_reuse.json';write_json(receipt,result)
    result['reuse_receipt_path']=str(receipt)
    return result


def preflight(plan, voice, bed, folder):
    """Use actual speech and keyframes before any accelerator task is claimable."""
    from .core.io import write_json, sha256
    from .plan import validate as validate_plan
    from .roughcut import freeze, render
    from .narrative_gate import review_manifest, require_review
    validate_plan(plan)
    missing = missing_previsuals(plan)
    if missing:
        raise ValueError('awaiting_storyboard: ' + ', '.join(missing))
    if plan.get('approved_story_preflight'):
        return reuse_preflight(plan,voice,bed,folder)
    effective = {**plan, 'narration': voice['effective_narration']}
    config = {'schema_version': 1, 'kind': 'editorial_roughcut',
              'duration_seconds': plan['duration_seconds'], 'caption_plan': effective,
              'narration_path': voice['audio_path'], 'music_path': bed['audio_path'],
              'shots': []}
    for s in plan['shots']:
        config['shots'].append(roughcut_row(s))
    if plan.get('locked_audio'):
        config['locked_audio'] = plan['locked_audio']
    report = render(freeze(config), Path(folder) / 'animatic')
    manifest = Path(folder) / 'effective_manifest.json'
    write_json(manifest, effective)
    review_path = Path(folder) / 'story_review.json'
    review_manifest(manifest, review_path)
    require_review(manifest, review_path)
    from . import media
    from .review import editorial_review
    contact = Path(folder) / 'keyframe_contact.jpg'
    media.review_contact(report['preview'], contact, plan=effective)
    visual_review = editorial_review(effective, contact, stage='preflight')
    from .review_failure import annotate
    visual_review = annotate(visual_review)
    visual_path = Path(folder) / 'keyframe_review.json'
    write_json(visual_path, visual_review)
    if visual_review.get('status') != 'automatic_review_passed':
        raise ValueError('keyframe animatic needs revision; see keyframe_review.json')
    return {'status': 'ready_for_generation', 'preview_path': report['preview'],
            'preview_sha256':sha256(report['preview']),
            'manifest_sha256':sha256(manifest),'visual_review_sha256':sha256(visual_path),
            'edl_sha256':sha256(Path(folder) / 'animatic/edl.json'),
            'manifest_path': str(manifest), 'review_path': str(review_path),
            'contact_path': str(contact), 'visual_review_path': str(visual_path),
            'edl_path': str(Path(folder) / 'animatic/edl.json'),
            'report_path': str(Path(folder) / 'animatic/report.json'),
            'creative_review_scope': 'text and sampled keyframe checks; not full-speed human acceptance'}
