"""W3 pre-extraction contracts: exact errors, issue order, and compiler side effects."""
import copy
import hashlib
import json
from unittest.mock import patch

import pytest

from workflow import film_ir, narrative_evidence, narrative_gate, planning_review_scope, storyboard
from test_narrative_evidence import sample
import test_narrative_gate as narrative_fixture
from test_planning_review_scope import answer
from test_storyboard_pipeline import film
import test_diagnosed_timing_revision as timing_fixture


def assign(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


@pytest.mark.parametrize('path,value,error', [
    (('story_contract', 'goal'), '', 'story_contract requires goal'),
    (('story_contract', 'beats'), {}, 'story requires causal beats'),
    (('story_contract', 'beats', 1, 'id'), 'load', 'story beats require unique ids'),
    (('story_contract', 'beats', 1, 'caused_by'), None, 'causal beats must link to the preceding beat'),
    (('story_contract', 'beats', 0, 'result'), '', 'causal beat requires result'),
    (('shots', 0, 'source_fit_policy'), 'trim', 'unknown source_fit_policy'),
    (('shots', 0, 'prompt'), '', 'shot needs an authored visual prompt'),
    (('shots', 0, 'storyboard', 'purpose'), '', 'S1: storyboard requires purpose'),
    (('shots', 0, 'storyboard', 'beat_id'), 'absent', 'shot references unknown story beat'),
    (('shots', 0, 'storyboard', 'state_before'), {}, 'shot needs complete before/after state with identical keys'),
    (('shots', 0, 'storyboard', 'transition', 'kind'), 'invalid', 'shot needs an explicit transition kind'),
    (('shots', 0, 'storyboard', 'transition', 'kind'), 'return', 'new scene needs establish or cutaway transition'),
    (('shots', 1, 'storyboard', 'transition', 'kind'), 'establish', 'existing scene must preserve its ledger on return'),
    (('shots', 1, 'storyboard', 'state_before', 'box'), 'missing', 'unexplained scene state jump in S2'),
    (('shots', 1, 'storyboard', 'beat_id'), 'load', 'shots must realize every causal beat once in chronological order'),
])
def test_storyboard_exact_rejections(path, value, error):
    plan = film()
    assign(plan, path, value)
    original = copy.deepcopy(plan)
    with pytest.raises(ValueError) as caught:
        storyboard.validate(plan)
    assert str(caught.value) == error
    assert plan == original


@pytest.mark.parametrize('path,value,error', [
    (('narrative_contract', 'central_question'), '', 'missing narrative central_question'),
    (('narrative_contract', 'beats'), [], '3–5 unique causal beats required'),
    (('narrative_contract', 'beats', 0, 'fact_ids'), ['missing'], 'unbound causal beat'),
    (('narrative_contract', 'shots', 0, 'id'), 'missing', 'narrative shots must cover actual film in chronological order'),
    (('narrative_contract', 'shots', 0, 'narration_index'), True, 'invalid narration line index'),
    (('narrative_contract', 'shots', 0, 'narration'), 'other', 'shot narration differs from executable voice script'),
    (('narrative_contract', 'shots', 0, 'beat_id'), 'missing', 'unknown narrative beat or fact'),
    (('narrative_contract', 'shots', 0, 'visible_action'), '', 'shot requires action and transition, not only camera angle'),
    (('narrative_contract', 'shots', 1, 'beat_id'), '0', 'causal beat has no visual realization'),
    (('narrative_contract', 'ending', 'result'), '', 'ending must resolve story with meaningful tradeoff/question'),
])
def test_narrative_gate_exact_rejections(path, value, error):
    plan = narrative_fixture.NarrativeTests().plan()
    assign(plan, path, value)
    original = copy.deepcopy(plan)
    with pytest.raises(ValueError) as caught:
        narrative_gate.validate_contract(plan)
    assert str(caught.value) == error
    assert plan == original


@pytest.mark.parametrize('path,value,error', [
    (('review_stage',), 'final', 'Planning stage declaration required'),
    (('checks',), [], 'Complete planning checks required'),
    (('checks', 0, 'criterion'), 'other', 'Planning criterion mismatch'),
    (('checks', 0, 'verdict'), 'approved', 'Invalid planning verdict'),
    (('checks', 0, 'confidence'), True, 'Invalid planning confidence'),
    (('checks', 0, 'confidence'), 1.01, 'Invalid planning confidence'),
    (('checks', 0, 'evidence_ids'), [], 'Evidence list required'),
    (('checks', 0, 'evidence_ids'), ['unknown'], 'Unknown planning evidence'),
    (('checks', 0, 'observable_basis'), '', 'Visible planning basis required'),
    (('checks', 0, 'confidence_reasons'), [], 'Explicit confidence reasons required'),
    (('checks', 0, 'confidence_reasons', 0, 'scope'), 'other', 'Invalid confidence reason'),
    (('findings',), {}, 'Findings and deferred checks required'),
    (('findings',), [{'severity': 'medium', 'issue': ''}], 'Invalid planning finding'),
    (('deferred_checks', 0, 'scope'), 'planning', 'Invalid deferred check'),
])
def test_planning_exact_rejections(path, value, error):
    observed = answer()
    assign(observed, path, value)
    original = copy.deepcopy(observed)
    with pytest.raises(ValueError) as caught:
        planning_review_scope.assess(observed, ['reach'], ['mid', 'end'])
    assert str(caught.value) == error
    assert observed == original


def test_planning_schema_order_and_review_identity():
    observed = answer(.72)
    observed['checks'][0]['confidence_reasons'] += [
        {'scope': 'audio', 'issue': 'Not heard.'}, {'scope': 'motion', 'issue': 'Not executed.'}]
    observed['findings'] = [{'severity': 'medium', 'issue': 'Collision.', 'evidence_ids': ['mid']}]
    actual = planning_review_scope.assess(observed, ['reach'], ['mid', 'end'])
    assert actual == {'policy_version': 'planning-observable-basis-v1', 'review_stage': 'preflight',
        'status': 'planning_review_required', 'confidence_threshold': .85, 'blocking_criteria': ['reach'],
        'material_findings': observed['findings'], 'scope_violations': [
            {'criterion': 'reach', 'scope': 'audio', 'issue': 'Not heard.'},
            {'criterion': 'reach', 'scope': 'motion', 'issue': 'Not executed.'}],
        'review': observed, 'automatic_replay': False, 'actual_motion_approved': False, 'whole_film_approved': False}
    assert actual['review'] is observed


def test_narrative_evidence_all_issue_order_snapshot():
    plan = sample()
    plan['shots'][0].update(cut_reason='', state_out={'boxes': 3}, action_complete_local=None,
        observations=[{'local_time': 1, 'state': {'boxes': 4}, 'evidence': 'frame'}])
    plan['shots'][1].update(axis='far', actors={}, role='evidence_insert', relation={'type': 'ellipsis'},
        action_complete_local=4, observations=[{}])
    plan['voice'] = [{'start': -.1, 'end': 5.8, 'measured_duration': 1},
                     {'start': 5.7, 'end': 6.1, 'measured_duration': .4}]
    plan['music'].update(source_audio_enabled=True, fade_start=4, fade_end=5, loop_after_fade=True)
    original = copy.deepcopy(plan)
    result = narrative_evidence.validate(plan)
    # Frozen on the unmodified W3 starting implementation, including every detail string.
    assert hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest() == '00aac5262b23adfad5853284fd72789f6c046e6c301cfe98154c3fc1eb2df726'
    assert plan == original


@pytest.mark.parametrize('field,value,error', [
    ('duration', 0, 'duration must be finite and positive'),
    ('duration', '6', 'duration must be finite and positive'),
    ('duplicate', True, 'duplicate shot id'),
    ('shot_duration', 0, 'shot duration must be finite and positive'),
    ('observation', 4, 'observation outside shot'),
])
def test_narrative_evidence_exact_exceptions(field, value, error):
    plan = sample()
    if field == 'duplicate':
        plan['shots'][1]['id'] = 'A'
    elif field == 'shot_duration':
        plan['shots'][0]['duration'] = value
    elif field == 'observation':
        plan['shots'][0]['observations'] = [{'local_time': value, 'evidence': 'frame', 'state': {}}]
    else:
        plan[field] = value
    with pytest.raises(ValueError) as caught:
        narrative_evidence.validate(plan)
    assert str(caught.value) == error


FILM_SNAPSHOTS = ['fee859c5da2059202e8a8b0f66caff9c2f82ccb7524aa6e4fcaa0f9f47e958a2', 'd0d88c054c1e2d67763984beae955d1ef3ec6a615e70c04cbb8e176edef3eb77', 'd0d88c054c1e2d67763984beae955d1ef3ec6a615e70c04cbb8e176edef3eb77', '59900136aa795168877a9fe38f382ff1fcd8ac1e65fa0e41b40eefd7916f0c4a', '6b4f0c679018c833e33a28e38f266261192d00f868d416295e38fa605b941da4', '22442305ca7658d328e5ca67aa9824c9711242310f8917d5fa11124b91504fef', '591977ea7ecd48c08aa2a16e878498971a78e6614723b9179365caed5851e84e', '611ab7047281b44739877f8f0b3b20984580bd758c1d2dc18c14136b97482d82', '3a30fea875da9713390126d03c6d0653d9fc0dcbb223c4fbfd64400d79b445aa', '6cfbea35891656bbbb60232fa7be50ab1d59f830bdef71f1a9f3737e1b309c76', '7d94b34533a6a87b25f16f7e9da1c680f9a1b23252886c844ab68e78e2ab81e5', 'e2df23c6a22ff9e40637ab2e895a8059564a8b448c2f9d3141f898f5c072651e']


@pytest.mark.parametrize('case', list(range(12)))
def test_film_ir_exact_output_and_io_sequence(case):
    shot = {'id': 'S1', 'slot_start': 0, 'slot_seconds': 4.5, 'seed': 7,
            'keyframes': {'start': ['start.png'], 'end': ['end.png']},
            'prompt_authored': 'Camera follows one complete action.'}
    profile = {'frames': 108, 'steps': 50, 'short_edge': 768}
    changes = [{}, {'conditioning': 'first_last'}, {'must_show': ['end']},
        {'conditioning': 'first_last', 'keyframes': {'start': ['start.png']}},
        {'continuity_bound': True, 'keyframes': {'start': ['start.png']}},
        {'continuity_bound': True, 'conditioning': 'first_only'},
        {'keyframes': {'start': ['start.png', 'extra.png']}},
        {'conditioning': 'first_last', 'keyframes': {'start': ['start.png', 'extra.png'], 'end': ['end.png', 'extra.png']}},
        {'keyframes': {'start': [None]}}, {'keyframes': {'start': ['absent.png']}},
        {'prompt_contract': 'v3_binding', 'motion': {'camera': {'type': 'hold'}}},
        {'prompt_contract': 'h3_fl2va_v1'}]
    shot.update(changes[case])
    trace = []
    def exists(path):
        trace.append(['exists', path])
        return path != 'absent.png'
    def sha(path):
        trace.append(['sha', path])
        return hashlib.sha256(path.encode()).hexdigest()
    violations = []
    with patch('os.path.isfile', side_effect=exists), patch.object(film_ir, '_file_sha', side_effect=sha):
        result = film_ir.compile_shot(shot, profile, violations)
    snapshot = {'result': result, 'violations': violations, 'trace': trace}
    assert hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest() == FILM_SNAPSHOTS[case]


@pytest.mark.parametrize('status,expected', [
    ('running', {'state': 'awaiting_source_settlement', 'source_job_id': 'source', 'running_shots': ['b']}),
    ('pending', {'state': 'awaiting_source_settlement', 'source_job_id': 'source', 'running_shots': ['b']}),
    ('leased', {'state': 'awaiting_source_settlement', 'source_job_id': 'source', 'running_shots': ['b']}),
])
def test_timing_revision_settlement_schema_precedes_visual_validation(status, expected):
    fixture = timing_fixture.TimingRevisionTests()
    fixture.setUp()
    try:
        fixture.db.execute('UPDATE tasks SET status=?,result=NULL WHERE id=?', (status, 'source:b'))
        fixture.db.commit()
        from workflow.diagnosed_revision import compile_timing_revision
        result = compile_timing_revision(fixture.root, 'source', {'a': 3, 'b': 3}, 'diagnosis', {'unknown': {}})
        assert result == expected
        assert not (fixture.root / 'user_repair30_20261002').exists()
    finally:
        fixture.tearDown()


@pytest.mark.parametrize('error,result,expected', [
    ('GPU OOM', None, 'Not a proven pre-dispatch timing failure: a'),
    ('execute: Generated action cannot fit this slot naturally; retime storyboard before dispatch', '{}', 'Failure does not match diagnosed timing: a'),
])
def test_timing_revision_exact_rejection(error, result, expected):
    fixture = timing_fixture.TimingRevisionTests()
    fixture.setUp()
    try:
        fixture.db.execute('UPDATE tasks SET error=?,result=? WHERE id=?', (error, result, 'source:a'))
        fixture.db.commit()
        from workflow.diagnosed_revision import compile_timing_revision
        with pytest.raises(ValueError) as caught:
            compile_timing_revision(fixture.root, 'source', {'a': 2, 'b': 4}, 'diagnosis')
        assert str(caught.value) == expected
        assert not (fixture.root / 'user_repair30_20261002').exists()
    finally:
        fixture.tearDown()


def test_timing_revision_write_and_validation_order():
    fixture = timing_fixture.TimingRevisionTests()
    fixture.setUp()
    try:
        from workflow import diagnosed_revision
        original_write = diagnosed_revision.write_json
        trace = []
        def write(path, value):
            trace.append(path.name)
            return original_write(path, value)
        with patch.object(diagnosed_revision, 'write_json', side_effect=write), \
                patch('workflow.plan.validate', side_effect=lambda plan: trace.append('validate')):
            receipt = fixture.run_compile()
            assert trace == ['timing_repair_budget.json', 'validate', 'manifest.json', 'compilation.json']
            assert receipt == fixture.run_compile()
            assert trace == ['timing_repair_budget.json', 'validate', 'manifest.json', 'compilation.json']
        assert receipt['state'] == 'requires_new_full_preflight'
        assert receipt['repaired_shots'] == ['a']
        assert receipt['pre_dispatch_timing_repairs'] == ['a']
    finally:
        fixture.tearDown()
