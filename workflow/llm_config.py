"""Resolve explicit v2 configuration; unversioned research profiles remain v1."""
import json
import os
from pathlib import Path

from .core.io import digest
from .llm import _TRANSPORTS

LANES = frozenset({'text', 'vision', 'final_review'})
TRANSPORTS = frozenset(_TRANSPORTS)
DECLARATIONS = frozenset({'config_version', 'extends', 'unset',
                          'vision_override', 'final_review_override'})


def _apply(config, values):
    """V2 null is a value; deletion requires an explicit, unambiguous unset."""
    values = dict(values)
    unset = values.pop('unset', [])
    if (not isinstance(unset, list)
            or any(not isinstance(key, str) or not key for key in unset)):
        raise ValueError('LLM config unset must be a list of nonempty field names')
    if set(unset).intersection(values):
        raise ValueError('LLM config cannot both set and unset the same field')
    for key in unset:
        config.pop(key, None)
    config.update(values)


def _load(path, ancestors=()):
    path = path.resolve()
    if path in ancestors:
        raise ValueError('LLM config extends cycle: ' + ' -> '.join(map(str, (*ancestors, path))))
    declaration = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(declaration, dict):
        raise ValueError('LLM config must be an object: ' + str(path))
    version = declaration.pop('config_version', 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unsupported LLM config_version: ' + str(version))
    parent = declaration.pop('extends', None)
    if version == 1:
        if parent is not None or 'unset' in declaration:
            raise ValueError('LLM config extends/unset requires config_version=2')
        return declaration, version
    config = {}
    if parent is not None:
        if not isinstance(parent, str) or not parent:
            raise ValueError('LLM config extends must be a nonempty file path')
        config, _ = _load(path.parent / parent, (*ancestors, path))
    _apply(config, declaration)
    return config, version


def _merge(config, override, version):
    if not isinstance(override, dict) or not override.get('model'):
        return False
    if version == 2:
        _apply(config, override)
    else:
        for key, value in override.items():
            if value is None:
                config.pop(key, None)
            else:
                config[key] = value
    return True


def resolve(lane, *, path=None):
    if lane not in LANES:
        raise ValueError('Unknown LLM lane: ' + str(lane))
    path = Path(path or os.environ.get('WORKFLOW_LLM_CONFIG',
                                      str(Path(__file__).parent / 'config/llm.json')))
    config, version = _load(path)
    vision = config.get('vision_override')
    final = config.get('final_review_override')
    if version == 2:
        for field in DECLARATIONS:
            config.pop(field, None)
    if os.environ.get('PROMPT_API_BASE'):
        config.update(base_url=os.environ['PROMPT_API_BASE'], api_key_env='PROMPT_API_KEY',
                      provider='explicit_override', transport='responses')
        for field in ('execution', 'cloud_worker', 'cloud_min_output_tokens'):
            config.pop(field, None)
    config['model'] = os.environ.get('PROMPT_MODEL') or config['model']
    if lane in ('vision', 'final_review'):
        if not _merge(config, vision, version):
            config['model'] = ((os.environ.get('REVIEW_MODEL') if lane == 'vision' else None)
                               or config.get('vision_model', config['model']))
    if lane == 'final_review':
        _merge(config, final, version)
    return config


def cache_identity(config):
    """Bind resolved runtime fields, excluding configuration declarations."""
    return digest({key: value for key, value in config.items() if key not in DECLARATIONS})


def check(config):
    if config.get('transport') not in TRANSPORTS:
        raise ValueError('Unknown LLM transport: ' + str(config.get('transport')))
    if (config.get('thinking') is not None and config.get('provider') != 'bigmodel'
            and config.get('execution') != 'cloud_ssh'):
        raise ValueError('thinking requires bigmodel or cloud_ssh')
    return config
