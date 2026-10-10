"""Read an authorized relay export without running shell or persisting secrets."""
import os
import re
import shlex
import tomllib
from pathlib import Path


def credential(config):
    source = config.get('relay_credential_source')
    if not source:
        return os.environ.get(config['api_key_env'])
    provider = tomllib.loads(Path(source['config_path']).expanduser().read_text(encoding="utf-8"))['model_providers'][source['provider']]
    if provider['base_url'].rstrip('/') != config['base_url'].rstrip('/') or provider['env_key'] != config['api_key_env']:
        raise ValueError('Relay credential endpoint mismatch')
    value = os.environ.get(config['api_key_env'])
    if value:
        return value
    raw = Path(source['shell_path']).expanduser().read_text(encoding="utf-8")
    matches = re.findall(r'^\s*export\s+' + re.escape(config['api_key_env']) + r'=(.*)$', raw, re.M)
    if not matches:
        raise ValueError('Relay export not found')
    parts = shlex.split(matches[-1], comments=True)
    if len(parts) != 1 or any(x in parts[0] for x in ('$', '`')):
        raise ValueError('Relay export must be a literal value')
    return parts[0]
