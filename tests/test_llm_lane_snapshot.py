"""Freeze each model profile against the existing configuration/review merge."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from workflow.llm import configuration
from workflow.llm_config import DECLARATIONS, LANES, TRANSPORTS, cache_identity, check, resolve

ROOT = Path(__file__).resolve().parents[1]
PROFILES = tuple(sorted(p for p in (ROOT / 'workflow/config').iterdir()
                        if p.is_file() and 'model' in json.loads(p.read_text())))
FIXTURES = ROOT / 'tests/fixtures/llm_lanes'


def legacy(lane):
    # Keep an independent pre-v2 oracle rather than calling the new resolver.
    path = Path(os.environ['WORKFLOW_LLM_CONFIG'])
    saved = FIXTURES / ('legacy_' + path.name)
    config = json.loads((saved if saved.exists() else path).read_text())
    config.pop('config_version', 1)
    migrated = json.loads(path.read_text()).get('config_version') == 2
    if os.environ.get('PROMPT_API_BASE'):
        config.update(base_url=os.environ['PROMPT_API_BASE'], api_key_env='PROMPT_API_KEY',
                      provider='explicit_override', transport='responses')
        for field in ('execution', 'cloud_worker', 'cloud_min_output_tokens'):
            config.pop(field, None)
    config['model'] = os.environ.get('PROMPT_MODEL') or config['model']
    if lane != 'text':
        override = config.get('vision_override')
        if isinstance(override, dict) and override.get('model'):
            for key, value in override.items():
                if value is None:
                    config.pop(key, None)
                else:
                    config[key] = value
        else:
            config['model'] = ((os.environ.get('REVIEW_MODEL') if lane == 'vision' else None)
                               or config.get('vision_model', config['model']))
    if lane == 'final_review':
        override = config.get('final_review_override')
        if isinstance(override, dict) and override.get('model'):
            for key, value in override.items():
                if value is None:
                    config.pop(key, None)
                else:
                    config[key] = value
    # V2 returns only effective fields; declarations no longer enter the cache.
    if migrated:
        return {key: value for key, value in config.items() if key not in DECLARATIONS}
    return config


class LaneSnapshotTests(unittest.TestCase):
    def test_every_profile_every_lane_matches_legacy_and_snapshot(self):
        for profile in PROFILES:
            for lane in sorted(LANES):
                with self.subTest(profile=profile.name, lane=lane), \
                     patch.dict(os.environ, {'WORKFLOW_LLM_CONFIG': str(profile)}, clear=True):
                    resolved = resolve(lane)
                    self.assertEqual(resolved, legacy(lane))
                    self.assertEqual(resolved, json.loads((FIXTURES / (profile.name + '.' + lane + '.json')).read_text()))
                    self.assertIs(check(resolved), resolved)
                    if lane != 'final_review':
                        self.assertEqual(resolved, configuration(visual=lane == 'vision'))

    def test_environment_merges_match_legacy(self):
        for profile in PROFILES:
            for lane in sorted(LANES):
                for env in ({'PROMPT_MODEL': 'text-test', 'REVIEW_MODEL': 'vision-test'},
                            {'PROMPT_API_BASE': 'https://invalid', 'PROMPT_MODEL': 'test'},
                            {'REVIEW_MODEL': 'vision-test'}):
                    with self.subTest(profile=profile.name, lane=lane, env=env), \
                         patch.dict(os.environ, dict(env, WORKFLOW_LLM_CONFIG=str(profile)), clear=True):
                        self.assertEqual(resolve(lane), legacy(lane))

    def test_null_deletes_and_missing_model_does_not_merge(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            path = Path(d) / 'config.json'
            config = {'model': 'base', 'transport': 'responses', 'thinking': {'type': 'disabled'},
                      'vision_override': {'model': 'vision', 'thinking': None},
                      'final_review_override': {'model': 'final', 'provider': None}}
            path.write_text(json.dumps(config))
            self.assertNotIn('thinking', resolve('vision', path=path))
            self.assertEqual(resolve('final_review', path=path)['model'], 'final')
            config['vision_override'] = {'thinking': None}
            path.write_text(json.dumps(config))
            self.assertIn('thinking', resolve('vision', path=path))
        with self.assertRaises(ValueError): resolve('typo')

    def test_transport_and_thinking_constraints(self):
        for transport in TRANSPORTS:
            self.assertEqual(check({'transport': transport}), {'transport': transport})
        for config in ({}, {'transport': 'typo'},
                       {'transport': 'responses', 'provider': 'robo', 'thinking': {}}):
            with self.assertRaises(ValueError): check(config)
        for config in ({'provider': 'bigmodel'}, {'execution': 'cloud_ssh'}):
            check(dict(config, transport='anthropic_messages', thinking={'type': 'disabled'}))
        config = {'transport': 'responses', 'model': 'one'}
        self.assertEqual(cache_identity(config), cache_identity(dict(reversed(list(config.items())))))
        self.assertNotEqual(cache_identity(config), cache_identity(dict(config, model='two')))

    def test_D8_review_profiles_full_effective_equality(self):
        # W1 removes the unused vision_draw_models key in the drain-only config cleanup.
        with patch.dict(os.environ, {}, clear=True):
            for lane in ('vision', 'final_review'):
                self.assertEqual(resolve(lane, path=ROOT / 'workflow/config/llm.json'),
                                 resolve(lane, path=ROOT / 'workflow/config/storyboard_review.json'))

    def test_review_profiles_consumed_fields_equal(self):
        with patch.dict(os.environ, {}, clear=True):
            for lane in ('vision', 'final_review'):
                left = resolve(lane, path=ROOT / 'workflow/config/llm.json')
                right = resolve(lane, path=ROOT / 'workflow/config/storyboard_review.json')
                self.assertEqual({k: v for k, v in left.items() if k != 'vision_draw_models'}, right)
