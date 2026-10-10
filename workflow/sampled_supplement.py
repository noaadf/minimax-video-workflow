"""Resolve confidence-only flags with explicit, immutable sampled evidence."""
import json
from pathlib import Path
from workflow.core.io import sha256

REQUIRED = {'single_vehicle_no_visible_fusion','same_man_vehicle_and_warehouse_across_samples','no_prominent_generated_text','cargo_present_consistently','closing_callback_matches_narration'}

def resolved_shots(audit_path):
    audit_path=Path(audit_path)
    index=audit_path.with_suffix('.supplements.json')
    if not index.exists():return set()
    audit=json.loads(audit_path.read_text(encoding="utf-8"));out=set()
    rows={r['shot_id']:r for r in audit['review']['shots']}
    evidence={r['shot_id']:r for r in audit['evidence']}
    for entry in json.loads(index.read_text(encoding="utf-8")).get('items',[]):
        p=Path(entry['path'])
        if sha256(p)!=entry['sha256']:raise ValueError('supplement changed')
        s=json.loads(p.read_text(encoding="utf-8"));sid=s['shot_id'];row=rows[sid];ev=evidence[sid]
        if s['audit_sha256']!=sha256(audit_path) or s['job_id']!=audit['job_id']:raise ValueError('supplement version mismatch')
        # Never override a negative verdict or an actual finding.
        if row.get('verdict')!='pass' or row.get('findings'):continue
        if s.get('verdict')!='pass' or any(f.get('severity')!='low' for f in s.get('findings',[])):continue
        if not REQUIRED.issubset(s.get('checks',{})) or not all(s['checks'][k] is True for k in REQUIRED):continue
        if s['video_sha256']!=ev['source_sha256'] or sha256(ev['video_path'])!=ev['source_sha256']:raise ValueError('supplement media changed')
        frames=s['frames']
        if [f['path'] for f in frames]!=ev['frames'] or [f['time_seconds'] for f in frames]!=ev['sample_seconds']:raise ValueError('supplement sample mismatch')
        if any(sha256(f['path'])!=f['sha256'] for f in frames):raise ValueError('supplement frames changed')
        out.add(sid)
    return out
