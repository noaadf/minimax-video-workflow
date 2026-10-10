"""Turn reviewed probe clips into immutable controller recipes, preserving script and audio."""
import copy
import json
from pathlib import Path
from .core.io import digest, sha256, write_json
from .plan import validate
from .repair import job_bundle


def prepare_clip_recipe(queue, parent_job_id, replacements, folder):
    """replacements maps exact shot IDs to probe folders with explicit acceptance."""
    if not replacements:
        raise ValueError('at least one replacement required')
    plan, results = job_bundle(queue, parent_job_id)
    valid = {s['id'] for s in plan['shots']}
    if set(replacements) - valid:
        raise ValueError('unknown target shot')
    approved = {}
    for shot_id, probe in replacements.items():
        path = Path(probe).resolve()
        acceptance = json.loads((path / 'acceptance.json').read_text(encoding="utf-8"))
        candidate = json.loads((path / 'result.json').read_text(encoding="utf-8"))
        source_plan = json.loads((path / 'plan.json').read_text(encoding="utf-8"))
        if acceptance.get('accepted_for_' + shot_id + '_replacement') is not True:
            raise ValueError('no explicit acceptance for ' + shot_id)
        checksum = candidate.get('sha256', {}).get(candidate['video_path'])
        if not checksum or sha256(candidate['video_path']) != checksum:
            raise ValueError('candidate clip changed or lacks receipt')
        if acceptance.get('video_sha256') != checksum:
            raise ValueError('acceptance must bind to the exact reviewed clip bytes')
        if parent_job_id not in acceptance.get('allowed_parent_jobs', []):
            raise ValueError('acceptance does not cover this parent narration/timeline')
        approved[shot_id] = {'acceptance': acceptance, 'candidate': candidate,
                            'prompt': source_plan['shots'][0]['prompt'],
                            'camera': source_plan['shots'][0].get('camera')}
    p = copy.deepcopy(plan)
    identity = digest({'recipe_version': 1, 'parent': parent_job_id, 'approved': approved})[:10]
    p.update(id=parent_job_id[:65] + '-ref-' + identity, parent_job_id=parent_job_id,
             root_job_id=plan.get('root_job_id', parent_job_id),
             repair_strategy='reviewed_reference_clips', conditioned_shot_review={
                 k: v['acceptance'] for k, v in approved.items()})
    p['prebuilt_narration'] = {'path': results['voice']['audio_path'],
                             'effective_narration': results['voice']['effective_narration'],
                             'segments': results['voice']['segments']}
    p['prebuilt_music'] = {'path': results['music']['audio_path'], 'config_sha256': digest(p['music'])}
    cursor = 0
    for shot in p['shots']:
        sid = shot['id']; asset = approved[sid]['candidate'] if sid in approved else results[sid]
        shot.update(source_path=asset['video_path'], source_start=0)
        if sid in approved:
            shot['prompt'] = approved[sid]['prompt']
            if approved[sid]['camera']:
                shot['camera'] = approved[sid]['camera']
            shot['use_native_foley'] = False
            shot.pop('foley_source_path', None)
            disclosure = approved[sid]['acceptance'].get('requires_disclosure')
            if disclosure:
                p.setdefault('visual_disclosures', []).append({'start': cursor,
                    'end': cursor + shot['duration_seconds'], 'text': disclosure})
        elif asset.get('foley_path'):
            shot['foley_source_path'] = asset['foley_path']
        cursor += shot['duration_seconds']
    for key in ('input_sha256', 'workflow_version', 'workflow_code_sha256', 'controller_intent'):
        p.pop(key, None)
    validate(p)
    target = Path(folder).resolve() / (p['id'] + '.json')
    write_json(target, p)
    recipe = {'parent_job_id': parent_job_id, 'manifest_path': str(target),
              'sha256': sha256(target), 'reviewed': True, 'backend': {'type': 'assets'},
              'review_scope': 'Exact reviewed clips and parent timeline only; remaining content blockers retained.',
              'replaced_shots': sorted(approved)}
    write_json(target.with_suffix('.recipe.json'), recipe)
    return recipe
