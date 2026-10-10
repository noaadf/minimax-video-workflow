"""Evidence-based follow-up taxonomy, separate from delivery approval.

Derive the taxonomy after aggregation; callers append findings to the review
returned by mark_review_required, so storing a classification there goes stale.
"""
import copy
import math


CONFIDENCE_THRESHOLD = 0.85
VISUAL_CATEGORIES = {'geometry', 'visual_quality', 'visual_repetition', 'visual_text_mismatch',
                     'generated_text', 'object_fusion', 'count_mutation'}
VISUAL_CRITERIA = {'structure', 'scene_contents', 'narration_alignment',
                   'action_result', 'state_continuity', 'composition'}
STATIC_UNOBSERVABLE = {'action_result', 'motion', 'continuous_motion', 'audio', 'listening'}


def _confidence_valid(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _reference_evidence(item):
    for key in ('shot_ids', 'evidence_ids'):
        refs = item.get(key)
        if isinstance(refs, list) and refs and all(_text(x) for x in refs):
            return True
    return _timestamp_evidence(item)


def _timestamp_evidence(item):
    for key in ('evidence_seconds', 'sample_seconds'):
        times = item.get(key)
        if isinstance(times, list) and times and all(
                type(t) in (int, float) and math.isfinite(t) and t >= 0 for t in times):
            return True
    return False


def _uncertain_finding(finding, stage):
    return (finding.get('requires_interpretation') is True
                     or finding.get('confirmed') is False
                     or finding.get('verdict') in ('review', 'uncertain')
                     or (stage == 'preflight' and finding.get('scope') in ('motion', 'audio')))


def _invalid_review_items(findings, checks):
    return (not isinstance(findings, list) or not isinstance(checks, list)
                      or any(not isinstance(x, dict) for x in (findings if isinstance(findings, list) else []))
                      or any(not isinstance(x, dict) for x in (checks if isinstance(checks, list) else [])))


def _classification_result(defects, reasons, findings):
    if defects:
        status, action, label = 'visual_rework_required', 'prepare_visual_rework', '画面有证据缺陷：返修'
    elif reasons:
        status, action, label = 'needs_human_attention', 'add_to_human_review', '人工待审：不自动重新生图'
    else:
        status, action, label = 'review_passed', 'record_advisories', '审核通过：小问题仅记录'
    return {'status': status, 'next_action': action, 'display_label': label,
            'confidence_threshold': CONFIDENCE_THRESHOLD,
            'reasons': list(dict.fromkeys(reasons)), 'visual_defects': copy.deepcopy(defects),
            'advisories': copy.deepcopy([f for f in findings if f.get('severity') == 'low']),
            'regeneration_eligible': bool(defects), 'automatic_regeneration': False,
            'delivery_approved': False}


def _evidence(item):
    if _reference_evidence(item):
        return True
    time = item.get('time_seconds')
    return ((type(time) in (int, float) and math.isfinite(time) and time >= 0)
            or _text(item.get('evidence')))


def _finding_defects(findings, stage, defects, reasons):
    for finding in findings:
        if finding.get('origin') == 'sha_bound_known_material_failure':
            defects.append(finding)
            continue
        if finding.get('severity') not in ('medium', 'high'):
            continue
        uncertain = _uncertain_finding(finding, stage)
        if (finding.get('category') in VISUAL_CATEGORIES and not uncertain
                and (_text(finding.get('issue')) or _text(finding.get('observation'))) and _evidence(finding)):
            defects.append(finding)
        else:
            reasons.append('unconfirmed_or_nonvisual_finding')


def _check_defects(checks, stage, defects):
    for check in checks:
        if (check.get('criterion') in VISUAL_CRITERIA and check.get('verdict') == 'reject'
                and not (stage == 'preflight' and check.get('criterion') in STATIC_UNOBSERVABLE)
                and _text(check.get('observation')) and _evidence(check)
                and check.get('requires_interpretation') is not True):
            defects.append(check)


def _review_reasons(review, checks, stage, reasons):
    for check in checks:
        confidence_reasons = check.get('confidence_reasons', [])
        if not isinstance(confidence_reasons, list):
            reasons.append('invalid_review_schema')
            confidence_reasons = []
        if stage == 'preflight' and (check.get('criterion') in STATIC_UNOBSERVABLE or any(
                isinstance(r, dict) and r.get('scope') in ('motion', 'audio')
                for r in confidence_reasons)):
            reasons.append('capability_mismatch')
        _confidence_reasons(check, reasons)
    if 'verdict' in review or not checks:
        _confidence_reasons(review, reasons)


def _review_items(findings, checks):
    schema_invalid = _invalid_review_items(findings, checks)
    findings = [x for x in findings if isinstance(x, dict)] if isinstance(findings, list) else []
    checks = [x for x in checks if isinstance(x, dict)] if isinstance(checks, list) else []
    return schema_invalid, findings, checks


def _confidence_reasons(check, reasons):
    if check.get('verdict') != 'pass':
        reasons.append('inconclusive_result')
    value = check.get('confidence')
    if not _confidence_valid(value) or value < CONFIDENCE_THRESHOLD:
        reasons.append('insufficient_confidence')


def classify_review(record, *, stage=None):
    """Return follow-up status/actions without editing receipts or approving media.

    Evidence means an attributable observation under the input schema; the
    caller must validate its media binding (audit_disposition does so for details).
    Missing motion in a static assessment is uncertainty, never a reroll request.
    """
    stage = stage or record.get('review_stage', 'final')
    review = record.get('review', record)
    review = review if isinstance(review, dict) else {}
    findings = review.get('findings')
    checks = review.get('checks', [])
    schema_invalid, findings, checks = _review_items(findings, checks)
    reasons = []
    defects = []
    _finding_defects(findings, stage, defects, reasons)
    _check_defects(checks, stage, defects)
    if schema_invalid or any(f.get('severity') not in ('low', 'medium', 'high') for f in findings):
        reasons.append('invalid_review_schema')
    if record.get('scope_violations') or review.get('capability_mismatch'):
        reasons.append('capability_mismatch')
    _review_reasons(review, checks, stage, reasons)
    if record.get('review_execution_status') == 'failed' or record.get('status') in ('audit_failed', 'failed'):
        reasons.append('review_unavailable')
    return _classification_result(defects, reasons, findings)


def review_dashboard(records):
    """Build separate read-only lists from fresh evidence, including legacy records."""
    lists = {'needs_human_attention': [], 'visual_rework_required': [], 'review_passed': []}
    for record in records:
        result = classify_review(record)
        lists[result['status']].append({'id': record.get('id', record.get('shot_id')), **result})
    return lists


def mark_review_required(editorial):
    """Retain receipts and findings, returning the review for additional findings."""
    editorial['status'] = 'review_required'
    review = editorial.setdefault('review', {'verdict': 'review', 'findings': []})
    if review.get('verdict') == 'pass':
        review['verdict'] = 'review'
    return review
