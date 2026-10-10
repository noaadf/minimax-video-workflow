"""W1 boundary regressions: no provider calls, SSH, or production state."""
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import urllib.error

import pytest

from workflow.core.failures import CODES, Disposition, WorkflowFailure
from workflow.core.io import write_json
from workflow.llm import _TRANSPORTS, ask_json
from workflow.llm_config import resolve
from workflow.review_failure import annotate
from workflow.storyboard_batch import transient_failure
from workflow.storyboard_deployment import PostSubmitUncertainty, admission_disposition


@pytest.mark.parametrize('transport', sorted(_TRANSPORTS))
def test_registry_and_cloud_execution_priority(transport):
    config = {'transport': transport, 'execution': 'cloud_ssh'}
    with patch('workflow.cloud_llm_client.ask', return_value=({'ok': True}, {})) as cloud, \
         patch('workflow.relay_credentials.credential') as credential:
        assert ask_json('prompt', {}, config=config) == ({'ok': True}, {})
    cloud.assert_called_once_with(config, 'prompt', {}, 16000, None, None)
    credential.assert_not_called()
    config.pop('execution')
    with patch(_TRANSPORTS[transport]+'.ask', return_value=({}, {})) as direct, \
         patch('workflow.relay_credentials.credential', return_value='fake'):
        assert ask_json('prompt', {}, config=config) == ({}, {})
    direct.assert_called_once()


def test_responses_payload_and_bounded_retries(tmp_path):
    from workflow.responses_client import ask
    image = tmp_path / 'frame.png'; image.write_bytes(b'fixture')
    response = {'id': 'id', 'model': 'actual', 'usage': {},
                'output': [{'content': [{'type': 'output_text', 'text': '{"ok":true}'}]}]}
    opener = Mock()
    opener.open.side_effect = [urllib.error.HTTPError('https://invalid', 503, 'retry', {}, None),
                              io.BytesIO(json.dumps(response).encode())]
    config = {'model': 'requested', 'provider': 'fake', 'base_url': 'https://invalid/',
              'reasoning_effort': 'high', 'service_tier': 'default'}
    with patch('workflow.relay_credentials.credential', return_value='fake'), \
         patch('workflow.responses_client.build_opener', return_value=opener), \
         patch('workflow.responses_client.time.sleep') as sleep:
        answer, receipt = ask(config, 'prompt', {'x': 1}, 123, [image])
    assert answer == {'ok': True}
    assert receipt['requested_model'] == 'requested'
    assert opener.open.call_count == 2
    sleep.assert_called_once_with(1)
    payload = json.loads(opener.open.call_args.args[0].data)
    assert payload['max_output_tokens'] == 123
    assert payload['input'][1]['content'][1]['image_url'].startswith('data:image/png;base64,')
    assert payload['reasoning'] == {'effort': 'high'}
    opener.open.side_effect = urllib.error.HTTPError('https://invalid', 400, 'terminal', {}, None)
    opener.reset_mock()
    with patch('workflow.relay_credentials.credential', return_value='fake'), \
         patch('workflow.responses_client.build_opener', return_value=opener), \
         pytest.raises(urllib.error.HTTPError):
        ask(config, 'prompt', {}, 123)
    assert opener.open.call_count == 1


def test_http_no_redirect_and_proxy_policy():
    from workflow.http_transport import NoRedirect, build_opener
    import urllib.request
    with patch('urllib.request.build_opener') as factory:
        build_opener({'use_proxy': False})
    handlers = factory.call_args.args
    assert isinstance(handlers[0], NoRedirect)
    assert handlers[1].proxies == {}
    with pytest.raises(urllib.error.HTTPError):
        handlers[0].redirect_request(urllib.request.Request('https://invalid'), None,
                                     302, 'redirect', {}, 'https://elsewhere')


@pytest.mark.parametrize('code', sorted(CODES))
def test_disposition_survives_disk_and_admission(tmp_path, code):
    write_json(tmp_path / 'keyframe_review.json', {'failure': WorkflowFailure(code).record()})
    detail = {}
    assert transient_failure(tmp_path, classification=detail) == (CODES[code] == Disposition.RETRY)
    assert detail['classification_source'] == 'failure'
    assert admission_disposition(WorkflowFailure(code)) == {
        Disposition.RETRY: 'held', Disposition.TERMINAL: 'rejected',
        Disposition.UNCERTAIN: 'isolated'}[CODES[code]]


def test_stale_retry_record_cannot_mask_real_code_failure(tmp_path):
    write_json(tmp_path / 'keyframe_review.json', {'failure': WorkflowFailure('provider_timeout').record()})
    for exc in (RuntimeError('bug'), KeyError('model'), TypeError('bug')):
        detail = {}
        assert transient_failure(tmp_path, exc, classification=detail) is False
        assert detail['classification_source'] == 'exception'
    wrapper = ValueError('keyframe animatic needs revision; see keyframe_review.json')
    assert transient_failure(tmp_path, wrapper)


def test_legacy_provenance_and_no_prefix_retry(tmp_path):
    write_json(tmp_path / 'story_review.json', {'status': 'failed', 'error_type': 'RuntimeError'})
    detail = {}
    assert not transient_failure(tmp_path, classification=detail)
    assert detail == {}  # W2: no classification is inferred from legacy text
    assert admission_disposition(ValueError('Required anything')) == 'rejected'
    assert admission_disposition(ValueError('Renderer receipt outside production root')) == 'rejected'
    uncertain = PostSubmitUncertainty('commit unknown')
    assert uncertain.code == 'submit_outcome_unknown'
    assert uncertain.effect == 'unknown'
    assert admission_disposition(uncertain) == 'isolated'


def test_annotation_preserves_review_reason_and_content():
    record = {'reason': 'review API unavailable or invalid response', 'error_type': 'KeyError',
              'diagnostic': {'failure': WorkflowFailure('contract_violation').record()}}
    result = annotate(record)
    assert result['reason'] == record['reason']
    assert result['failure']['disposition'] == 'terminal'
    assert 'failure' not in record
    content = {'status': 'review_required', 'review': {'verdict': 'reject'}}
    assert annotate(content) == content


@pytest.mark.parametrize('state,returncode,expected', [
    ('calling', 1, 'transport_network'), ('calling', 0, 'cloud_job_pending'),
    ('failed', 0, 'cloud_job_failed')])
def test_cloud_failures_are_typed_without_replay(state, returncode, expected):
    from workflow.cloud_llm_client import ask
    config = {'model': 'glm-5.3', 'cloud_worker': {'port': 22, 'identity_file': '/unused',
              'host': 'unused', 'code_root': '/code', 'state_root': '/state', 'wait_seconds': -1}}
    result = SimpleNamespace(returncode=returncode, stdout=json.dumps({
        'state': state, 'diagnostic': {'code': 'provider_http_error', 'http_status': 503}}))
    with patch('workflow.cloud_llm_client.subprocess.run', return_value=result) as run, \
         pytest.raises(WorkflowFailure) as caught:
        ask(config, 'prompt', {}, 10)
    assert caught.value.code == expected
    run.assert_called_once()
    if state == 'failed':
        assert caught.value.disposition == Disposition.TERMINAL
        assert caught.value.evidence['recoverable'] is True


def test_missing_storyboard_config_never_uses_planning(tmp_path):
    from scripts.run_user_repair30 import storyboard_review_config
    config = tmp_path / 'workflow/config'; config.mkdir(parents=True)
    (config / 'planning.json').write_text('{}')
    with pytest.raises(FileNotFoundError):
        storyboard_review_config(tmp_path)
    (config / 'storyboard_review.json').write_text('{}')
    assert storyboard_review_config(tmp_path) == 'storyboard_review.json'


def test_final_lane_ignores_vision_environment_without_final_override(tmp_path):
    path = tmp_path / 'llm.json'
    write_json(path, {'model': 'text', 'vision_model': 'vision', 'transport': 'responses'})
    with patch.dict(os.environ, {'REVIEW_MODEL': 'override'}, clear=True):
        assert resolve('vision', path=path)['model'] == 'override'
        assert resolve('final_review', path=path)['model'] == 'vision'


def test_config_failure_preserves_code_through_frozen_review(tmp_path):
    from workflow.review import editorial_review
    contact = tmp_path / 'contact.jpg'; contact.write_bytes(b'fixture')
    config = {'model': 'fake', 'provider': 'fake', 'base_url': 'https://invalid',
              'transport': 'typo'}
    with patch('workflow.review.resolve', return_value=config), \
         patch.dict(os.environ, {'WORKFLOW_REVIEW_CACHE': str(tmp_path / 'cache')}):
        result = editorial_review({'shots': [], 'narration': []}, contact, stage='preflight')
    assert annotate(result)['failure']['code'] == 'config_invalid'


@pytest.mark.parametrize('code', ['provider_unavailable', 'cloud_job_failed', 'config_invalid'])
def test_narrative_producer_disk_consumer_contract(tmp_path, code):
    from workflow.narrative_gate import review_manifest
    manifest = tmp_path / 'manifest.json'
    write_json(manifest, {'facts': [], 'shots': [], 'narration': []})
    review = tmp_path / 'story_review.json'
    error = WorkflowFailure(code, 'fixture failure')
    with patch('workflow.narrative_gate.validate_contract', return_value={}), \
         pytest.raises(WorkflowFailure) as caught:
        review_manifest(manifest, review, Mock(side_effect=error))
    assert caught.value is error
    record = json.loads(review.read_text())
    assert record['failure']['code'] == code
    assert record['classification_source'] == 'exception'
    assert transient_failure(tmp_path) == (CODES[code] == Disposition.RETRY)
