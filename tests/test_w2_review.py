import json
import os
from unittest.mock import patch

import pytest

from workflow.core.failures import WorkflowFailure, classify
from workflow.core.io import digest, sha256, write_json
from workflow.code_identity import code_identity
from workflow.review import editorial_review
from workflow.runner import StageError


def test_final_review_model_and_cache_ignore_review_model(tmp_path):
    config = tmp_path / 'config.json'
    write_json(config, {'model': 'final-model', 'vision_model': 'vision-model',
                       'provider': 'fixture', 'transport': 'responses', 'base_url': 'https://invalid'})
    image = tmp_path / 'contact.jpg'; image.write_bytes(b'fixture')
    plan = {'shots': [], 'narration': []}
    answer = ({'verdict': 'pass', 'findings': []}, {'model': 'fixture'})
    env = {'WORKFLOW_LLM_CONFIG': str(config), 'WORKFLOW_REVIEW_CACHE': str(tmp_path / 'cache')}
    with patch.dict(os.environ, dict(env, REVIEW_MODEL='vision-one'), clear=True), \
         patch('workflow.review.ask_json', return_value=answer) as ask:
        editorial_review(plan, image)
        assert ask.call_args.kwargs['model'] == 'vision-model'
        assert ask.call_args.kwargs['config']['model'] == 'vision-model'
        os.environ['REVIEW_MODEL'] = 'vision-two'
        assert editorial_review(plan, image)['cache_hit']
        assert ask.call_count == 1
        editorial_review(plan, image, stage='preflight')
        assert ask.call_args.kwargs['model'] == 'vision-two'


def test_old_cache_is_retained_but_never_hit(tmp_path):
    from workflow import review
    config = {'model': 'fixture', 'provider': 'fixture', 'base_url': 'https://invalid'}
    image = tmp_path / 'contact.jpg'; image.write_bytes(b'fixture')
    plan = {'shots': [], 'narration': []}
    brief = {k: plan.get(k) for k in ('title', 'facts', 'narration', 'shots', 'continuity_bible',
             'visual_disclosures', 'comment_prompt', 'story_contract', 'visual_style_policy', 'color_policy')}
    old_key = digest({'version': 'editorial-cache-v2-stage-bound', 'stage': 'final',
                     'prompt_code': code_identity(review.__file__), 'image': sha256(image),
                     'brief': brief, 'model': 'fixture', 'provider': 'fixture',
                     'api_base': 'https://invalid', 'reasoning_effort': 'medium', 'service_tier': 'default'})
    old = tmp_path / 'cache' / (old_key + '.json')
    write_json(old, {'status': 'old-cache-sentinel'})
    original = old.read_bytes()
    with patch.dict(os.environ, {'WORKFLOW_REVIEW_CACHE': str(old.parent)}, clear=True), \
         patch('workflow.review.resolve', return_value=config), \
         patch('workflow.review.ask_json', return_value=({'verdict': 'pass', 'findings': []}, {'id': 'new'})) as ask:
        assert editorial_review(plan, image)['status'] == 'automatic_review_passed'
        assert ask.call_count == 1
        assert editorial_review(plan, image)['cache_hit']
    assert old.read_bytes() == original
    assert len(list(old.parent.glob('*.json'))) == 2


@pytest.mark.parametrize('retryable,uncertain,code', [(False,False,'unclassified'),
    (True,False,'runtime_not_adopted'), (True,True,'submit_outcome_unknown')])
def test_stage_error_keeps_queue_contract(retryable, uncertain, code):
    error = StageError('unchanged message', retryable, uncertain)
    assert isinstance(error, WorkflowFailure)
    assert classify(error) is error
    assert error.code == code
    assert (error.retryable, error.uncertain) == (retryable, uncertain)
    assert str(error) == 'unchanged message'


def test_configuration_error_has_failure(tmp_path):
    with patch('workflow.review.resolve', side_effect=KeyError('broken config')):
        result = editorial_review({}, tmp_path / 'unused.jpg')
    assert result['failure']['code'] == 'contract_violation'
    assert result['reason'] == 'Review configuration or cache unavailable'


def test_cache_tracks_loaded_prompt_not_new_code_on_disk(tmp_path):
    from workflow import review
    source = tmp_path / 'review.py'; source.write_text('prompt = "old"\n')
    image = tmp_path / 'contact.jpg'; image.write_bytes(b'fixture')
    config = {'model': 'fixture', 'provider': 'fixture', 'base_url': 'https://invalid'}
    with patch.dict(os.environ, {'WORKFLOW_REVIEW_CACHE': str(tmp_path / 'cache')}, clear=True), \
         patch.object(review, '__file__', str(source)), \
         patch('workflow.review.resolve', return_value=config), \
         patch('workflow.review.ask_json', return_value=({'verdict': 'pass', 'findings': []}, {'id': 'loaded'})) as ask:
        editorial_review({'shots': [], 'narration': []}, image)
        source.write_text('prompt = "new on disk, not loaded"\n')
        assert editorial_review({'shots': [], 'narration': []}, image)['cache_hit']
        assert ask.call_count == 1
        # A reloaded module gets a new namespace; existing cached files remain.
        with patch.object(review, 'REVIEW_CODE_SHA256', code_identity(source)):
            assert not editorial_review({'shots': [], 'narration': []}, image).get('cache_hit')
        assert ask.call_count == 2
