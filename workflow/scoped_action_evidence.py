"""Validate supplemental observations without rewriting model scores or queue state."""
import json
from pathlib import Path
from .core.io import sha256
from .shot_review import assess


def validate(path, source_video):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    quality_path = Path(document['original_quality_path'])
    if sha256(quality_path) != document['original_quality_sha256']:
        raise ValueError('Original quality evidence changed')
    if document.get('reviewer') != 'codex_sampled_visual_review' or document.get('content_approved') is not False:
        raise ValueError('Supplemental review identity or scope invalid')
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    rows = quality['shot_review']['shots']
    supplements = {s['shot_id']: s for s in document['shots']}
    if len(supplements) != len(document['shots']):
        raise ValueError('Duplicate supplemental shot')
    media_sha = sha256(source_video)
    accepted, limitations = [], []
    held = {r['shot_id'] for r in rows if r['status'] != 'sampled_checks_passed'}
    if set(supplements) != held:
        raise ValueError('Supplemental review must cover exactly held shots')
    for row in rows:
        if row.get('review_execution_status') != 'completed' or not row.get('receipt'):
            raise ValueError('Incomplete model review cannot be adjudicated')
        if row['binding']['video_sha256'] != media_sha:
            raise ValueError('Source media changed')
        computed = assess(row['review'], row['sample_times'], row['binding']['criteria'])
        if computed != row['status']:
            raise ValueError('Stored model status inconsistent')
        if row['shot_id'] not in held:
            continue
        checks = row['review']['checks']
        if any(c['verdict'] != 'pass' for c in checks) or any(f['severity'] != 'low' for f in row['review']['findings']):
            raise ValueError('Only confidence-only holds qualify')
        supplement = supplements[row['shot_id']]
        for key in ('binding_sha256', 'sample_times', 'evidence_sheet', 'evidence_sha256', 'endpoint_evidence'):
            if supplement.get(key) != row.get(key):
                raise ValueError('Supplemental evidence binding mismatch')
        if sha256(row['evidence_sheet']) != row['evidence_sha256']:
            raise ValueError('Sample sheet changed')
        endpoint = row['endpoint_evidence']
        if sha256(endpoint['path']) != endpoint['sha256']:
            raise ValueError('Endpoint sheet changed')
        if not supplement.get('observation') or supplement.get('original_scores_unchanged') is not True:
            raise ValueError('Missing independent observation')
        disposition = supplement.get('disposition')
        if disposition not in ('sampled_action_and_result_observed', 'minor_staging_limitation_no_regeneration'):
            raise ValueError('Unknown scoped disposition')
        accepted.append(row['shot_id'])
        limitations.append({'shot_id': row['shot_id'], 'disposition': disposition, 'observation': supplement['observation']})
    return {'status': 'supplemental_evidence_validated', 'resolved_confidence_holds': accepted,
            'limitations': limitations, 'original_scores_changed': False, 'delivery_approved': False,
            'source_video_sha256': media_sha, 'supplement_sha256': sha256(path),
            'scope': 'Evidence validation only. Minor staging acceptance does not certify exact authored movement. Final version, audio, export and product acceptance remain separate.'}


def validate_dense_review(quality_path, plan, source_video, supplement_path):
    """Verify full-film per-shot evidence before a downstream revision can reuse it."""
    quality_path = Path(quality_path)
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    document = json.loads(Path(supplement_path).read_text(encoding="utf-8"))
    if Path(document['original_quality_path']).resolve() != quality_path.resolve():
        raise ValueError('Supplement belongs to another quality report')
    scoped = validate(supplement_path, source_video)
    rows = quality['shot_review']['shots']
    expected = [s['id'] for s in plan['shots']]
    if len(set(expected)) != len(expected) or [r['shot_id'] for r in rows] != expected:
        raise ValueError('Missing, duplicate or reordered shot reviews')
    cursor = 0
    for shot, row in zip(plan['shots'], rows):
        if row['binding']['shot'] != shot:
            raise ValueError('Authored shot differs from reviewed shot')
        if abs(row['global_start'] - cursor) > .001:
            raise ValueError('Reviewed shot timeline shifted')
        times = row['sample_times']
        # Dense grids select 24 targets at duration/24, not at duration/23.
        # Validate their actual cadence; retain the unsampled tail explicitly.
        length = shot['duration_seconds']
        dense_coverage = (len(times) == 24 and times[0] <= .06
                          and all(abs(t - i * length / 24) <= .06 for i, t in enumerate(times))
                          and all(a < b for a, b in zip(times, times[1:]))
                          and all(0 <= t < length for t in times))
        if not dense_coverage:
            raise ValueError('Dense review lacks beginning/middle/end coverage')
        if sha256(row['evidence_sheet']) != row['evidence_sha256']:
            raise ValueError('Dense review sheet changed')
        endpoint = row['endpoint_evidence']
        if endpoint['times'] != [times[0], times[-1]] or sha256(endpoint['path']) != endpoint['sha256']:
            raise ValueError('Dense endpoint evidence changed')
        cursor += shot['duration_seconds']
    if abs(cursor - plan['duration_seconds']) > .01:
        raise ValueError('Reviewed timeline incomplete')
    return {**scoped, 'status': 'dense_review_evidence_validated',
            'quality_sha256': sha256(quality_path), 'shot_ids': expected,
            'sample_count': sum(len(r['sample_times']) for r in rows),
            'unsampled_tail_seconds': {s['id']: round(s['duration_seconds'] - r['sample_times'][-1], 4) for s, r in zip(plan['shots'], rows)},
            'delivery_approved': False}
