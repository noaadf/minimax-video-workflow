"""Classify audit findings for follow-up, never approve delivery or mutate evidence."""
from pathlib import Path
from .core.io import sha256
from .review_state import classify_review


def _bound_detail(detail, evidence):
    """A missing/changed artifact requires attention, not a confirmed defect."""
    try:
        return (detail.get('review_execution_status') == 'completed'
                and detail.get('binding', {}).get('video_sha256') == evidence.get('source_sha256')
                and Path(evidence.get('video_path', '')).is_file()
                and sha256(evidence['video_path']) == evidence.get('source_sha256')
                and Path(detail.get('evidence_sheet', '')).is_file()
                and sha256(detail['evidence_sheet']) == detail.get('evidence_sha256'))
    except (OSError, KeyError, TypeError):
        return False

def disposition(record):
    if record.get('status') == 'audit_failed' or not record.get('finished_at'):
        return {'state':'incomplete_audit','delivery_approved':False,'shots':[],
                'review_state':'needs_human_attention','next_action':'add_to_human_review',
                'automatic_regeneration':False}
    evidence={x['shot_id']:x for x in record.get('evidence',[])}
    details={x['shot_id']:x for x in record.get('exceptions',[])}
    results=[]
    classified=[]
    for row in record.get('review',{}).get('shots',[]):
        sid=row['shot_id']; findings=row.get('findings',[]); detail=details.get(sid)
        outcome='review_required';basis='initial_audit'
        taxonomy=classify_review(row)
        if any(f.get('origin')=='sha_bound_known_material_failure' for f in findings):
            outcome='material_repair_required'
        elif detail:
            ev=evidence.get(sid,{})
            valid=_bound_detail(detail,ev)
            if valid:
                basis='bound_detailed_review'
                taxonomy=classify_review(detail)
                checks=detail.get('review',{}).get('checks',[])
                f=detail.get('review',{}).get('findings',[])
                complete=({c.get('criterion') for c in checks} in (
                    {'structure','scene_contents','narration_alignment'},
                    {'structure','scene_contents','narration_alignment','action_result','state_continuity'})
                    and len(checks)==len({c.get('criterion') for c in checks}))
                if not complete and taxonomy['status']=='review_passed':
                    taxonomy={**taxonomy,'status':'needs_human_attention',
                              'next_action':'add_to_human_review',
                              'display_label':'人工待审：详细检查项不完整',
                              'reasons':taxonomy['reasons']+['incomplete_detailed_checks']}
                if any(c.get('verdict')=='reject' for c in checks) or any(x.get('severity') in ('medium','high') for x in f):
                    outcome='material_review_required'
                elif (complete and taxonomy['status']=='review_passed'
                      and f and all(x.get('severity')=='low' for x in f)):
                    outcome='minor_only_no_regeneration';basis='bound_detailed_review'
            else:
                # Do not promote an unbound detailed observation to repair evidence.
                # The independently attributable initial findings remain intact.
                if taxonomy['status']!='visual_rework_required':
                    taxonomy={**taxonomy,'status':'needs_human_attention',
                              'next_action':'add_to_human_review',
                              'display_label':'人工待审：详细证据未绑定',
                              'reasons':taxonomy['reasons']+['unbound_detailed_review']}
        elif row.get('verdict')=='pass' and not findings:
            outcome='sampled_pass' if taxonomy['status']=='review_passed' else 'uncertainty_only_no_regeneration'
        elif any(f.get('severity') in ('medium','high') for f in findings):
            outcome='material_review_required'
        results.append({'shot_id':sid,'disposition':outcome,'basis':basis,
                        'review_state':taxonomy['status'],
                        **{k:v for k,v in taxonomy.items() if k!='status'}})
        classified.append({'shot_id':sid,'taxonomy':taxonomy})
    # Preserve legacy dispositions consumed by run_user_repair30; the new lanes
    # are explicit and never authorize a model call, generation or delivery.
    dashboard={name:[] for name in ('needs_human_attention','visual_rework_required','review_passed')}
    for item in classified:
        dashboard[item['taxonomy']['status']].append({'shot_id':item['shot_id'],**item['taxonomy']})
    return {'state':'triaged','delivery_approved':False,'shots':results,
            'dashboard':dashboard,
            'limits':['No audio, continuous-motion or final delivery approval.','Original model records remain authoritative evidence; uncertainty is not a confirmed defect.']}
