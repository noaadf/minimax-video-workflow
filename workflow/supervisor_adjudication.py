"""Supervisor adjudication of uncertainty holds; never overrides confirmed defects.

A delegation entry for the human-review slot the strict gate expects. It clears
only capability-mismatch holds (motion/action uncertainty from static sampling)
when the supervisor supplies frame-sequence evidence bound to the exact video
SHA. Rejections, material findings and changed media invalidate the record.
"""
import json
from pathlib import Path

from .core.io import sha256

VERSION = 'supervisor-adjudication-v1'
ALLOWED_DISPOSITIONS = ('motion_verified_by_frame_sequence', 'confidence_hold_cleared')


def validate(record_path, quality_path, source_video):
    """Return the adjudicated shot ids, or raise. Never mutates any receipt."""
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    if record.get('version') != VERSION:
        raise ValueError('Unknown adjudication version')
    if record.get('reviewer') != 'claude-supervisor':
        raise ValueError('Adjudication identity invalid')
    if record.get('content_approved') is not False:
        raise ValueError('Adjudication must not self-approve content')
    if sha256(quality_path) != record.get('quality_sha256'):
        raise ValueError('Quality evidence changed after adjudication')
    if sha256(source_video) != record.get('video_sha256'):
        raise ValueError('Source media changed after adjudication')
    rows = json.loads(Path(quality_path).read_text(encoding="utf-8"))['shot_review']['shots']
    by_id = {r['shot_id']: r for r in rows}
    held = {r['shot_id'] for r in rows if r.get('status') != 'sampled_checks_passed'}
    cleared = {}
    editorial = record.get('editorial')
    if editorial is not None:
        quality = json.loads(Path(quality_path).read_text(encoding="utf-8"))
        review = quality.get('editorial', {}).get('review', {})
        if editorial.get('disposition') != 'confidence_hold_cleared':
            raise ValueError('Unknown editorial disposition')
        if review.get('verdict') == 'reject':
            raise ValueError('Editorial rejection cannot be adjudicated clear')
        from .delivery_policy import is_blocking
        if any(is_blocking(f) for f in review.get('findings', []) if isinstance(f, dict)):
            raise ValueError('Editorial material findings require rework, not adjudication')
        if not editorial.get('observation'):
            raise ValueError('Editorial adjudication requires an observation')
    for item in record.get('shots', []):
        shot_id, disposition = item.get('shot_id'), item.get('disposition')
        if shot_id not in held or disposition not in ALLOWED_DISPOSITIONS:
            raise ValueError('Adjudication targets a non-held or unknown shot: ' + str(shot_id))
        review = by_id[shot_id].get('review') or {}
        if review.get('verdict') == 'reject':
            raise ValueError('Rejected shots cannot be adjudicated clear')
        if any(f.get('severity') in ('medium', 'high') and f.get('category') != 'unknown'
               for f in review.get('findings', [])):
            raise ValueError('Confirmed material findings require rework, not adjudication')
        evidence = item.get('evidence_frames') or []
        if not evidence or not all(
                isinstance(Path(f).is_file() and sha256(f), str) and
                item.get('evidence_sha256', {}).get(Path(f).name) == sha256(f)
                for f in evidence):
            raise ValueError('Frame evidence missing or unbound')
        cleared[shot_id] = item
    if set(cleared) != held:
        raise ValueError('Adjudication must cover exactly the held shots')
    return cleared, editorial


def apply_to(quality, cleared, editorial=None):
    """Return a derived shot-review view with uncertainty holds resolved.

    Original statuses stay untouched in the stored receipt; this view is what
    delivery_policy consumes, mirroring how supplements resolve without edits.
    The editorial hold, when adjudicated, becomes the explicit
    'supervisor_adjudicated' status — never a forged model approval.
    """
    import copy
    view = copy.deepcopy(quality)
    for row in view['shot_review']['shots']:
        item = cleared.get(row['shot_id'])
        if item is None:
            continue
        row['status'] = 'sampled_checks_passed'
        row['supervisor_adjudication'] = {
            'disposition': item['disposition'],
            'observation': item.get('observation', ''),
            'evidence': [Path(f).name for f in item.get('evidence_frames', [])],
        }
    unresolved = [r['shot_id'] for r in view['shot_review']['shots']
                  if r['status'] != 'sampled_checks_passed']
    if unresolved:
        view['shot_review']['status'] = 'review_required'
    else:
        view['shot_review']['status'] = 'sampled_checks_passed'
    if editorial is not None:
        view.setdefault('editorial', {})['status'] = 'supervisor_adjudicated'
        view['editorial']['supervisor_adjudication'] = {
            'disposition': editorial['disposition'],
            'observation': editorial.get('observation', ''),
        }
        view['editorial_status'] = 'supervisor_adjudicated'
    return view
