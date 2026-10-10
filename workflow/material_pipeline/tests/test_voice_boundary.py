import json
import pytest

from workflow.material_pipeline.production_bridge import _normalize_material_voice, _require_resume_audit
from workflow.material_pipeline.core import sha256, write_json


@pytest.mark.parametrize('pitch', ['+0Hz', '0Hz', '-0Hz', 0])
def test_minimax_natural_pitch_has_integer_payload(pitch):
    p = {'voice': {'engine': 'minimax', 'pitch': pitch}, 'narration': []}
    _normalize_material_voice(p)
    assert type(p['voice']['pitch']) is int
    assert p['voice']['pitch'] == 0


def test_edge_pitch_is_preserved_and_line_voice_is_normalized():
    p = {'voice': {'name': 'edge-voice', 'pitch': '+0Hz'},
         'narration': [{'voice': {'engine': 'minimax', 'pitch': '+0Hz'}}]}
    _normalize_material_voice(p)
    assert p['voice']['pitch'] == '+0Hz'
    assert p['narration'][0]['voice']['pitch'] == 0


@pytest.mark.parametrize('pitch', ['-18Hz', 'bad', True, 13, -13, 1.5])
def test_unknown_pitch_requires_explicit_corrected_voice(pitch):
    with pytest.raises(ValueError, match='integer pitch'):
        _normalize_material_voice({'voice': {'engine': 'minimax', 'pitch': pitch}})


def audit_fixture(root):
    item = root/'material_intake'/'candidate'
    request = item/'production_request.json'
    write_json(request, {'content_id': 'candidate'})
    outcome = root/'external.json'
    write_json(outcome, {'job_id': 'model-job', 'state': {'state': 'completed'}})
    worker = root/'material_intake'/'production'/'worker'
    status = worker/'worker_status.json'
    write_json(status, {'state': 'attention', 'phase': 'whole_film_voice', 'finished': 123,
                        'error': 'minimax t2a error 2013: integer pitch required'})
    write_json(worker/'worker_attempt.json', {'request_sha256': sha256(request)})
    audit = {'package_sha256': 'packet-hash', 'external_outcomes_known': True, 'reason': 'correct voice parameters',
             'prior_external_calls': [{'path': str(outcome), 'sha256': sha256(outcome),
                                       'job_id': 'model-job', 'state': 'completed'}],
             'preparation_terminal_outcome': {'status_path': str(status), 'status_sha256': sha256(status),
                                              'request_sha256': sha256(request)}}
    write_json(item/'resume_audit.json', audit)
    return item, worker, status, audit


def test_known_pre_audio_parameter_failure_can_resubmit(tmp_path):
    audit_fixture(tmp_path)
    _require_resume_audit(tmp_path, 'candidate', {'state': 'production_attention', 'package_sha256': 'packet-hash'})


@pytest.mark.parametrize('change', ['request', 'unknown_outcome', 'wrong_attempt', 'successful_audio', 'other_failure'])
def test_preparation_resubmission_does_not_replay_ambiguous_or_successful_work(tmp_path, change):
    item, worker, status, audit = audit_fixture(tmp_path)
    if change == 'request':
        write_json(item/'production_request.json', {'content_id': 'new-request'})
    elif change == 'wrong_attempt':
        write_json(worker/'worker_attempt.json', {'request_sha256': 'other'})
    elif change == 'successful_audio':
        (worker/'whole_voice.mp3').write_bytes(b'already synthesized')
    else:
        body = json.loads(status.read_text())
        if change == 'unknown_outcome':body['state'] = 'preparing'
        else:body['error'] = 'network failure with unknown outcome'
        write_json(status, body)
        audit['preparation_terminal_outcome']['status_sha256'] = sha256(status)
        write_json(item/'resume_audit.json', audit)
    with pytest.raises(ValueError):
        _require_resume_audit(tmp_path, 'candidate', {'state': 'production_attention', 'package_sha256': 'packet-hash'})
