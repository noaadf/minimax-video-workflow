"""V2 declarations preserve runtime routing and raw research consumers offline."""
import io
import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from workflow.anthropic_client import ask
from workflow.core.io import write_json
from workflow.llm import configuration
from workflow.llm_config import DECLARATIONS, cache_identity, resolve

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/llm_lanes'


def profile(tmp_path, **values):
    path = tmp_path / 'profile.json'
    write_json(path, {'config_version': 2, 'model': 'text', 'transport': 'responses', **values})
    return path


def test_v2_null_is_preserved_and_unset_is_explicit(tmp_path):
    path = profile(tmp_path, provider='base', thinking={'type': 'disabled'},
                   vision_override={'model': 'vision', 'thinking': None, 'unset': ['provider']},
                   final_review_override={'model': 'final', 'unset': ['thinking']})
    with patch.dict(os.environ, {}, clear=True):
        vision = resolve('vision', path=path)
        final = resolve('final_review', path=path)
    assert vision['thinking'] is None
    assert 'provider' not in vision
    assert 'thinking' not in final
    assert final['model'] == 'final'
    assert DECLARATIONS.isdisjoint(vision)
    assert DECLARATIONS.isdisjoint(final)


def test_override_without_model_still_does_not_apply(tmp_path):
    path = profile(tmp_path, thinking={'type': 'disabled'},
                   vision_override={'unset': ['thinking']})
    with patch.dict(os.environ, {}, clear=True):
        assert resolve('vision', path=path)['thinking'] == {'type': 'disabled'}


def test_relative_extends_and_top_level_unset(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.mkdir()
    base = source / 'base.json'
    write_json(base, {'config_version': 2, 'model': 'base', 'transport': 'responses',
                     'provider': 'base', 'thinking': {'type': 'disabled'},
                     'vision_override': {'model': 'vision', 'unset': ['thinking']}})
    child = source / 'child.json'
    write_json(child, {'config_version': 2, 'extends': 'base.json',
                      'unset': ['provider'], 'model': 'child', 'optional': None})
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ, {}, clear=True):
        text = resolve('text', path=child)
        vision = resolve('vision', path=child)
    assert text['model'] == 'child'
    assert text['optional'] is None
    assert 'provider' not in text
    assert text['thinking'] == {'type': 'disabled'}
    assert vision['model'] == 'vision'
    assert 'thinking' not in vision
    assert DECLARATIONS.isdisjoint(text)


@pytest.mark.parametrize('values,match', [
    ({'config_version': 3}, 'config_version'),
    ({'config_version': True}, 'config_version'),
    ({'extends': []}, 'extends'),
    ({'extends': ''}, 'extends'),
    ({'unset': None}, 'unset'),
    ({'unset': 'provider'}, 'unset'),
    ({'unset': [1]}, 'unset'),
    ({'unset': ['']}, 'unset'),
    ({'unset': ['model']}, 'both set and unset'),
    ({'config_version': 1, 'unset': []}, 'config_version=2'),
    ({'config_version': 1, 'extends': 'base.json'}, 'config_version=2'),
])
def test_invalid_v2_declarations_fail_explicitly(tmp_path, values, match):
    with pytest.raises(ValueError, match=match):
        resolve('text', path=profile(tmp_path, **values))


def test_missing_parent_cycle_and_non_object_fail(tmp_path):
    path = profile(tmp_path, extends='missing.json')
    with pytest.raises(FileNotFoundError, match='missing.json'):
        resolve('text', path=path)
    write_json(path, {'config_version': 2, 'extends': 'profile.json'})
    with pytest.raises(ValueError, match='extends cycle'):
        resolve('text', path=path)
    other = tmp_path / 'other.json'
    write_json(other, {'config_version': 2, 'extends': 'profile.json'})
    write_json(path, {'config_version': 2, 'extends': 'other.json'})
    with pytest.raises(ValueError, match='profile.json.*other.json.*profile.json'):
        resolve('text', path=path)
    write_json(path, [])
    with pytest.raises(ValueError, match='must be an object'):
        resolve('text', path=path)


def test_v2_environment_precedence_and_final_isolation(tmp_path):
    path = profile(tmp_path, provider='base', vision_model='vision',
                   execution='cloud_ssh', cloud_worker={'host': 'unused'},
                   cloud_min_output_tokens=100)
    env = {'WORKFLOW_LLM_CONFIG': str(path), 'PROMPT_API_BASE': 'https://invalid',
           'PROMPT_MODEL': 'prompt', 'REVIEW_MODEL': 'review'}
    with patch.dict(os.environ, env, clear=True):
        text, vision, final = (resolve(lane) for lane in ('text', 'vision', 'final_review'))
        assert configuration() == text
        assert configuration(visual=True) == vision
    assert [c['model'] for c in (text, vision, final)] == ['prompt', 'review', 'vision']
    for config in (text, vision, final):
        assert config['base_url'] == 'https://invalid'
        assert config['api_key_env'] == 'PROMPT_API_KEY'
        assert config['provider'] == 'explicit_override'
        assert not {'execution', 'cloud_worker', 'cloud_min_output_tokens'} & config.keys()


def test_cache_identity_uses_effective_fields_only():
    config = {'model': 'model', 'transport': 'responses', 'thinking': None}
    declarations = {'config_version': 2, 'extends': 'elsewhere.json', 'unset': ['unused'],
                    'vision_override': {'model': 'unused'}, 'final_review_override': {}}
    assert cache_identity(config) == cache_identity({**config, **declarations})
    assert cache_identity(config) != cache_identity({**config, 'model': 'other'})
    assert cache_identity(config) != cache_identity({key: value for key, value in config.items()
                                                   if key != 'thinking'})


def test_storyboard_extends_preserves_raw_research_http_contract(tmp_path):
    current = json.loads((ROOT / 'workflow/config/storyboard_review.json').read_text())
    previous = json.loads((FIXTURES / 'legacy_storyboard_review.json').read_text())
    assert current['config_version'] == 2
    assert current['extends'] == 'llm.json'
    assert 'vision_override' not in current
    assert 'final_review_override' not in current
    image = tmp_path / 'frame.jpg'; image.write_bytes(b'offline-image')
    response = {'content': [{'type': 'text', 'text': '{"ok":true}'}],
                'stop_reason': 'end_turn', 'model': 'kimi-k3', 'usage': {'output_tokens': 4}}
    captured = []
    credential_fields = ('api_key_env', 'base_url', 'shell_settings_source', 'cc_switch_provider')
    for config in (previous, current):
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps(response).encode())
        with patch('workflow.anthropic_client.credential', return_value='offline') as credential, \
             patch('workflow.http_transport.build_opener', return_value=opener) as build, \
             patch('workflow.anthropic_client.time.monotonic', return_value=1):
            result = ask(config, 'same instruction', {'same': 'data'}, 1500, [image])
        request = opener.open.call_args.args[0]
        captured.append((request.full_url, request.data, dict(request.header_items()),
                         opener.open.call_args.kwargs, result,
                         {key: credential.call_args.args[0].get(key) for key in credential_fields},
                         build.call_args.args[0].get('use_proxy')))
    assert captured[0] == captured[1]
    assert current['timeout_seconds'] == 420
    assert current['total_timeout_seconds'] == 1200
    assert current['stream'] is False
