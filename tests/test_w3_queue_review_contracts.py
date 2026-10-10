"""W3 pre-extraction contracts: review taxonomy, queue transitions, image claims."""
import copy
import hashlib
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from workflow import queue, review_state, shot_review, storyboard_image_jobs


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def taxonomy_cases():
    base = {'severity': 'high', 'category': 'geometry', 'issue': 'bent edge', 'shot_ids': ['S1']}
    for stage, severity, extra in itertools.product(
            ('final', 'preflight'), ('low', 'medium', 'high', 'bad'),
            ({}, {'confirmed': False}, {'requires_interpretation': True},
             {'scope': 'motion'}, {'verdict': 'uncertain'}, {'category': 'subtitle'},
             {'origin': 'sha_bound_known_material_failure'}, {'shot_ids': []})):
        yield {'review_stage': stage, 'review': {'verdict': 'pass', 'confidence': .9,
               'findings': [{**base, 'severity': severity, **extra}]}}, None
    for stage, criterion, verdict, confidence in itertools.product(
            ('final', 'preflight'), ('structure', 'action_result', 'audio'),
            ('pass', 'reject', 'review'), (.84, .85, True, None)):
        yield {'review': {'findings': [], 'checks': [{'criterion': criterion,
               'verdict': verdict, 'confidence': confidence, 'observation': 'observed',
               'evidence_seconds': [0]}]}}, stage
    for review in ({}, {'findings': [None], 'checks': [None]},
                   {'findings': [], 'checks': None}, {'findings': [], 'verdict': 'pass', 'confidence': .9}):
        yield {'status': 'failed', 'scope_violations': ['motion'], 'review': review}, None
    yield {'review_stage': 'preflight', 'review': {'findings': [], 'checks': [
        {'criterion': 'structure', 'verdict': 'pass', 'confidence': .9,
         'confidence_reasons': [{'scope': 'audio'}]}]}}, None
    yield {'review': {'findings': [], 'checks': [{'confidence_reasons': 'invalid'}]}}, None


def test_taxonomy_full_schema_order_and_copy_snapshot():
    rows = []
    for record, stage in taxonomy_cases():
        before = copy.deepcopy(record)
        result = review_state.classify_review(record, stage=stage)
        assert record == before
        rows.append(result)
        if result['visual_defects']:
            result['visual_defects'][0]['test_mutation'] = True
            assert record == before
    assert fingerprint(rows) == '9bc21a3ee9ab25cc88342b99e0314a1d52ce830fa41d175032313344145564b7'


def answer():
    return {'checks': [{'criterion': c, 'verdict': 'pass', 'confidence': .95,
                       'evidence_seconds': [0, .5], 'observation': 'observed'}
                      for c in shot_review.CRITERIA], 'findings': [], 'not_assessed': ['audio']}


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('verdict', 'invalid', 'invalid verdict'),
    ('confidence', True, 'invalid confidence'),
    ('confidence', float('nan'), 'invalid confidence'),
    ('evidence_seconds', [], 'no evidence'),
    ('observation', ' ', 'no evidence'),
    ('evidence_seconds', [9], 'evidence outside supplied sample timestamps'),
])
def test_shot_assess_error_text_and_precedence(field, value, message):
    value_answer = answer()
    value_answer['checks'][0][field] = value
    value_answer['findings'] = None
    with pytest.raises(ValueError, match='^' + message + '$'):
        shot_review.assess(value_answer, [0, .5])


@pytest.mark.parametrize(('finding', 'message'), [
    ({'severity': 'bad', 'issue': 'x', 'time_seconds': 0}, 'invalid finding'),
    ({'severity': 'low', 'issue': ' ', 'time_seconds': 0}, 'invalid finding'),
    ({'severity': 'high', 'issue': 'x', 'time_seconds': True}, 'invalid finding time'),
    ({'severity': 'high', 'issue': 'x', 'time_seconds': 1}, 'invalid finding time'),
])
def test_shot_finding_contract(finding, message):
    value_answer = answer(); value_answer['findings'] = [finding]
    with pytest.raises(ValueError, match='^' + message + '$'):
        shot_review.assess(value_answer, [0, .5])


def test_queue_transitive_expiry_scope_and_event_order(tmp_path):
    q = queue.Queue(tmp_path / 'queue.sqlite')
    tasks = [{'id': name, 'stage': 'speech' if name == 'speech' else 'shot',
              'capability': 'gpu' if name == 'gpu' else 'cpu', 'payload': {}, 'deps': deps}
             for name, deps in [('cpu', []), ('gpu', []), ('speech', []),
                                ('child', ['gpu']), ('grandchild', ['child']), ('other', [])]]
    q.submit('film', 'digest', {}, tasks)
    with q.connect() as c:
        c.execute("UPDATE tasks SET status='running', attempts=3, token='old', lease_until=1 WHERE id IN ('cpu','gpu','speech')")
        c.execute("UPDATE tasks SET job_id='untouched' WHERE id='other'")
    with patch('workflow.queue.time.time', return_value=10), patch('workflow.queue.uuid.uuid4', return_value=SimpleNamespace(hex='token')):
        assert q.claim(['cpu', 'gpu'], now=10, job_ids={'film'}) is None
    assert [(r['id'], r['status'], r['error']) for r in q.status()] == [
        ('cpu', 'failed', 'lease expired; outcome may need remote reconciliation'),
        ('gpu', 'reconcile', 'lease expired; outcome may need remote reconciliation'),
        ('speech', 'reconcile', 'lease expired; outcome may need remote reconciliation'),
        ('child', 'blocked', 'dependency failed'), ('grandchild', 'blocked', 'dependency failed'),
        ('other', 'pending', None)]
    with q.connect() as c:
        assert [tuple(r) for r in c.execute("SELECT task_id,event,detail FROM events ORDER BY seq")] == [
            ('film', 'submitted', 'digest'), ('cpu', 'lease_expired', ''),
            ('speech', 'lease_expired', ''), ('gpu', 'lease_expired', '')]


def test_queue_claim_return_and_effect_order(tmp_path):
    q = queue.Queue(tmp_path / 'queue.sqlite')
    groups = [{'type': 'ascend_ssh', 'ready': True, 'capability': x, 'resource_id': x,
               'host': x, 'port': 22, 'expires_at': '2099-01-01T00:00:00Z'} for x in ('slow', 'fast')]
    payload = {'plan': {'backend': {'type': 'pool', 'scheduling': 'work_conserving', 'groups': groups}}, 'data': {'id': 'S1'}}
    q.submit('film', 'digest', {}, [{'id': 'shot', 'stage': 'shot', 'capability': 'slow', 'payload': payload}])
    with patch('workflow.queue.time.time', return_value=10), patch('workflow.queue.uuid.uuid4', return_value=SimpleNamespace(hex='token')):
        result = q.claim(['fast'], now=10)
    expected_payload = {**payload, 'backend_override': groups[1]}
    assert result == {'id': 'shot', 'job_id': 'film', 'stage': 'shot', 'capability': 'fast',
                      'payload': expected_payload, 'deps': [], 'status': 'pending', 'attempts': 1,
                      'max_attempts': 3, 'token': 'token', 'lease_until': None, 'available_at': 0,
                      'result': None, 'error': None}
    with q.connect() as c:
        events = [tuple(r) for r in c.execute('SELECT task_id,event,detail FROM events ORDER BY seq')]
        assert events == [('film', 'submitted', 'digest'), ('shot', 'dispatch_bound', json.dumps({
            'from': 'slow', 'to': 'fast', 'resource_id': 'fast',
            'reason': 'idle eligible group claims next unstarted shot'})), ('shot', 'claimed', 'token')]
        assert tuple(c.execute('SELECT status,capability,attempts,token,lease_until FROM tasks').fetchone()) == ('running', 'fast', 1, 'token', 130)


@pytest.mark.parametrize(('extra', 'reason'), [
    ({'aspect_ratio': 'bad'}, 'invalid_manifest_canvas'),
    ({'image_generation_strategy': 'independent_text_keyframes_reviewed_as_board'},
     'independent_strategy_requires_shared_spec_and_no_authored_references'),
])
def test_image_claim_isolation_snapshot(tmp_path, extra, reason):
    plan = {**extra, 'shots': [{'id': 'S1', 'prompt': 'scene',
             'keyframe_prompts': {'start': 'before', 'end': 'after'},
             'storyboard': {'state_before': {}, 'state_after': {}}}]}
    manifest = tmp_path / 'manifest.json'; manifest.write_text(json.dumps(plan))
    jobs = storyboard_image_jobs.ImageJobs(tmp_path / 'images'); jobs.add(manifest, 'film')
    assert jobs.claim(owner='operator') is None
    for path in jobs.folder.glob('*.json'):
        row = json.loads(path.read_text())
        assert row['state'] == 'isolated' and row['reason'] == reason
        assert 'claim_token' not in row and 'external_executor' not in row


@pytest.mark.parametrize('failure', [None, TimeoutError('private'), RuntimeError('SLAI official client redacted'), ValueError('schema detail')])
def test_shot_review_cache_and_call_order(tmp_path, monkeypatch, failure):
    calls = []
    video = tmp_path / 'video'; video.write_bytes(b'video')
    def configuration(**kwargs):
        calls.append('configuration'); return {'model': 'test', 'provider': 'test', 'base_url': 'test'}
    def sample(video, sheet, **kwargs):
        calls.append('sample'); Path(sheet).write_bytes(b'sheet'); return [0, .5]
    def ask(*args, **kwargs):
        calls.append('ask')
        if failure: raise failure
        return answer(), {'receipt': 'kept'}
    write = shot_review.write_json
    def save(path, record):
        calls.append(('write', record.get('review_execution_status'))); write(path, record)
    monkeypatch.setattr(shot_review, 'configuration', configuration)
    monkeypatch.setattr(shot_review, 'sample_sheet', sample)
    monkeypatch.setattr(shot_review, 'ask_json', ask)
    monkeypatch.setattr(shot_review, 'write_json', save)
    monkeypatch.delenv('REVIEW_MODEL', raising=False)
    result = shot_review.review_shot(video, {'id': 'S1', 'duration_seconds': 1}, tmp_path / 'reviews')
    assert calls == ['configuration', 'sample', 'ask', ('write', 'failed' if failure else 'completed')]
    assert result['content_approved'] is False
    if failure:
        assert {key: result[key] for key in ('status', 'error_type', 'review_execution_status', 'reason')} == {
            'status': 'review_required', 'error_type': type(failure).__name__, 'review_execution_status': 'failed',
            'reason': 'API unavailable or invalid evidence/schema'}
        if isinstance(failure, ValueError): assert result['validation_failure'] == str(failure)
        if isinstance(failure, RuntimeError): assert result['client_failure_summary'] == str(failure)
    else:
        assert result['status'] == 'sampled_checks_passed'
        assert shot_review.review_shot(video, {'id': 'S1', 'duration_seconds': 1}, tmp_path / 'reviews') == result
        assert calls[-1] == 'configuration'
