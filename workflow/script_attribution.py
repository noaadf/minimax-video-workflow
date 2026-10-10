"""Non-secret authorship evidence bound to the exact spoken script."""
import hashlib
import json


def record(plan, receipt, role):
    script = [segment['text'] for segment in plan['narration']]
    fingerprint = hashlib.sha256(json.dumps(script, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    event = {'role': role, 'script_sha256': fingerprint,
             'provider': receipt.get('provider', 'unknown'),
             'requested_model': receipt.get('requested_model'),
             'reported_model': receipt.get('model'),
             'request_id': receipt.get('id') or receipt.get('request_id')}
    events = plan.setdefault('script_authorship', [])
    if not events or events[-1] != event:
        events.append(event)
    return events
