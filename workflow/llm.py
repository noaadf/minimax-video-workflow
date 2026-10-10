"""Bounded JSON dispatch with explicit transport registration."""
from importlib import import_module
from .core.failures import WorkflowFailure, classify
from .http_transport import NoRedirect  # compatibility re-export


def configuration(*, visual=False):
    # Import lazily: llm_config validates transports against this registry.
    from .llm_config import resolve
    return resolve('vision' if visual else 'text')


_TRANSPORTS = {
    'responses': 'workflow.responses_client',
    'anthropic_messages': 'workflow.anthropic_client',
    'claude_cli': 'workflow.claude_cli_client',
    'codex_agentic': 'workflow.codex_agentic_client',
    'codex_cli': 'workflow.codex_client',
    'chat_completions': 'workflow.chat_client',
}


def ask_json(instruction, data, max_tokens=16000, *, image_paths=None, model=None, config=None):
    try:
        if config is None:
            config = configuration(visual=bool(image_paths))
        transport = config.get('transport')
        if transport not in _TRANSPORTS:
            raise WorkflowFailure('config_invalid', 'Unknown LLM transport',
                                  evidence={'transport': transport})
        if config.get('execution') == 'cloud_ssh':
            from .cloud_llm_client import ask
        else:
            # These legacy clients expect relay credentials to have been validated
            # by the dispatcher. Subscription clients deliberately bypass this.
            if transport in ('codex_cli', 'chat_completions'):
                from .relay_credentials import credential
                if not credential(config):
                    raise ValueError(config['api_key_env']+' required for '+config['provider'])
            ask = import_module(_TRANSPORTS[transport]).ask
        return ask(config, instruction, data, max_tokens, image_paths, model)
    except Exception as exc:
        # Frozen review.py already persists diagnostic. Preserve the original
        # exception and message while carrying lossless classification across it.
        exc.diagnostic = {**(getattr(exc, 'diagnostic', None) or {}),
                          'failure': classify(exc).record()}
        raise
