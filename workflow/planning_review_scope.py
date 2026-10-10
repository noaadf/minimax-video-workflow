"""Opt-in planning evidence contract; never converts a motion hold to approval."""
import math

from .core.io import digest

VERSION = 'planning-observable-basis-v1'
THRESHOLD = 0.85


def instruction():
    return '''STAGE=preflight; confidence concerns visible PLANNING evidence only.
Judge the authored causal action/result, feasible keyframe geometry, planned state
continuity and composited framing. A static reference is a plan, never proof of
an executed action. Reject a genuinely absent planned result or visible geometry
contradiction. Missing actual interpolation, true 3D clearance, full motion and
audio listening belong in deferred_checks, not confidence_reasons.
Return one JSON object with review_stage="preflight" and checks containing the
supplied criteria exactly once. Every check requires verdict="pass|review|reject",
confidence in [0,1], evidence_ids from supplied evidence, observable_basis describing
specific visible planning evidence, and confidence_reasons=[{scope:"planning",
issue:"specific visible/planned ambiguity or reason for certainty"}]. Never call a
future generated action observable. Preserve every material finding in findings
as {severity:"low|medium|high",issue:string,evidence_ids:[]}; a visible collision
cannot be hidden in deferred_checks. deferred_checks contains only
{scope:"motion|audio",issue:string}. A low planning confidence still blocks.
This review cannot approve actual action/state completion or a finished film.'''


def request_binding(manifest_sha256, evidence_hashes, payload, *, previous_evidence_hashes=None):
    """Distinct policy/stage binding for genuinely new review requests only."""
    if previous_evidence_hashes is not None and sorted(evidence_hashes.values()) == sorted(previous_evidence_hashes.values()):
        raise ValueError('Unchanged evidence cannot be replayed through a new policy')
    return digest({'version': VERSION, 'stage': 'preflight',
                   'manifest_sha256': manifest_sha256,
                   'evidence_hashes': evidence_hashes, 'payload': payload})


def assess(answer, criteria, evidence_ids):
    """Validate declared score scope and retain strict scores/findings unchanged.

    Scope declarations do not prove a model's reasoning. Inconsistent declarations
    require attention, not automatic replay or an adjusted score.
    """
    if not criteria or len(set(criteria)) != len(criteria):
        raise ValueError('Unique planning criteria required')
    checks = _planning_checks(answer, criteria)
    allowed = set(evidence_ids)

    scope_violations = []
    for check in checks:
        _planning_check(check, allowed)
        _confidence_reasons(check, scope_violations)
    findings = answer.get('findings')
    deferred = answer.get('deferred_checks')
    if not isinstance(findings, list) or not isinstance(deferred, list):
        raise ValueError('Findings and deferred checks required')
    _planning_findings(findings, allowed)
    _deferred_checks(deferred)
    return _assessment_result(answer, checks, findings, scope_violations)


def _planning_checks(answer, criteria):
    if answer.get('review_stage') != 'preflight':
        raise ValueError('Planning stage declaration required')
    checks = answer.get('checks')
    if not isinstance(checks, list) or len(checks) != len(criteria) or any(not isinstance(c, dict) for c in checks):
        raise ValueError('Complete planning checks required')
    if {c.get('criterion') for c in checks} != set(criteria):
        raise ValueError('Planning criterion mismatch')
    return checks


def _refs(values, allowed, *, required):
    if not isinstance(values, list) or (required and not values):
        raise ValueError('Evidence list required')
    if any(not isinstance(x, str) or x not in allowed for x in values):
        raise ValueError('Unknown planning evidence')


def _sentence(value):
    return isinstance(value, str) and bool(value.strip())



def _planning_check(check, allowed):
    if check.get('verdict') not in ('pass', 'review', 'reject'):
        raise ValueError('Invalid planning verdict')
    confidence = check.get('confidence')
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('Invalid planning confidence')
    _refs(check.get('evidence_ids'), allowed, required=True)
    if not _sentence(check.get('observable_basis')):
        raise ValueError('Visible planning basis required')


def _confidence_reasons(check, scope_violations):
    reasons = check.get('confidence_reasons')
    if not isinstance(reasons, list) or not reasons:
        raise ValueError('Explicit confidence reasons required')
    for reason in reasons:
        if not isinstance(reason, dict) or not _sentence(reason.get('issue')) or reason.get('scope') not in ('planning', 'motion', 'audio'):
            raise ValueError('Invalid confidence reason')
        if reason['scope'] != 'planning':
            scope_violations.append({'criterion': check['criterion'], **reason})


def _planning_findings(findings, allowed):
    for finding in findings:
        if not isinstance(finding, dict) or finding.get('severity') not in ('low', 'medium', 'high') or not _sentence(finding.get('issue')):
            raise ValueError('Invalid planning finding')
        _refs(finding.get('evidence_ids'), allowed, required=True)


def _deferred_checks(deferred):
    for item in deferred:
        if not isinstance(item, dict) or item.get('scope') not in ('motion', 'audio') or not _sentence(item.get('issue')):
            raise ValueError('Invalid deferred check')


def _assessment_result(answer, checks, findings, scope_violations):
    blockers = [c['criterion'] for c in checks if c['verdict'] != 'pass' or c['confidence'] < THRESHOLD]
    material_findings = [f for f in findings if f['severity'] in ('medium', 'high')]
    ready = not blockers and not material_findings and not scope_violations
    return {'policy_version': VERSION, 'review_stage': 'preflight',
            'status': 'planning_ready' if ready else 'planning_review_required',
            'confidence_threshold': THRESHOLD, 'blocking_criteria': blockers,
            'material_findings': material_findings, 'scope_violations': scope_violations,
            'review': answer, 'automatic_replay': False,
            'actual_motion_approved': False, 'whole_film_approved': False}
