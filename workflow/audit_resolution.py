"""Append-only, media-bound human/agent adjudication of sampled audit uncertainty.

Never resolves a failed API, a rejected shot, a high-severity finding, or a
known material failure. Original model evidence remains intact.
"""
import json
from pathlib import Path
from .core.io import sha256


def resolved_shots(audit_path):
    audit_path = Path(audit_path)
    index = audit_path.with_suffix('.resolutions.json')
    if not index.exists():
        return set()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if not audit.get('finished_at') or audit.get('status') == 'audit_failed' or not audit.get('receipt'):
        return set()
    rows = {r['shot_id']: r for r in audit['review']['shots']}
    evidence = {r['shot_id']: r for r in audit['evidence']}
    resolved = set()
    for entry in json.loads(index.read_text(encoding="utf-8"))['items']:
        path = Path(entry['path'])
        if sha256(path) != entry['sha256']:
            raise ValueError('resolution changed')
        r = json.loads(path.read_text(encoding="utf-8"))
        if r['audit_sha256'] != sha256(audit_path) or r['job_id'] != audit['job_id']:
            raise ValueError('resolution belongs to different audit')
        sid = r['shot_id']; row = rows[sid]; ev = evidence[sid]
        if row.get('verdict') == 'reject' or any(f.get('severity') == 'high' or f.get('origin') == 'sha_bound_known_material_failure' for f in row.get('findings', [])):
            continue
        if r.get('disposition') != 'advisory_no_regeneration' or r.get('reviewer') != 'codex_sampled_visual_review':
            continue
        if not r.get('reason') or not r.get('limitations') or r.get('content_approved') is not False:
            continue
        if r['video_sha256'] != ev['source_sha256'] or sha256(ev['video_path']) != ev['source_sha256']:
            raise ValueError('resolution media changed')
        frames = r['evidence_files']
        allowed = set(ev.get('frames', []))
        allowed.update(x['evidence_sheet'] for x in audit.get('exceptions', []) if x.get('shot_id') == sid and x.get('evidence_sheet'))
        if not frames or any(f['path'] not in allowed for f in frames):
            raise ValueError('resolution evidence is not part of this shot audit')
        if any(sha256(f['path']) != f['sha256'] for f in frames):
            raise ValueError('resolution visual evidence changed')
        # Bind every original finding, so a later new finding cannot be silently covered.
        if r['original_findings'] != row.get('findings', []):
            raise ValueError('resolution does not cover current findings')
        if r.get('no_visible_material_defect') is True:
            resolved.add(sid)
    return resolved
