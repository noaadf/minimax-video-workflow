"""Portable failure codes; external exception interpretation has one home."""
from collections.abc import Mapping
from enum import Enum
import http.client
import subprocess
import urllib.error


class Disposition(str, Enum):
    RETRY = 'retry'
    TERMINAL = 'terminal'
    UNCERTAIN = 'uncertain'


CODES: dict[str, Disposition] = {
    'provider_timeout': Disposition.RETRY,
    'provider_unavailable': Disposition.RETRY,
    'provider_rate_limited': Disposition.RETRY,
    'provider_stream_error': Disposition.RETRY,
    'transport_network': Disposition.RETRY,
    'cloud_job_pending': Disposition.RETRY,
    'runtime_not_adopted': Disposition.RETRY,
    'local_io': Disposition.RETRY,
    'model_output_invalid': Disposition.RETRY,
    'provider_rejected_request': Disposition.TERMINAL,
    'output_truncated': Disposition.TERMINAL,
    'cloud_job_failed': Disposition.TERMINAL,
    'config_invalid': Disposition.TERMINAL,
    'contract_violation': Disposition.TERMINAL,
    'code_defect': Disposition.TERMINAL,
    'unclassified': Disposition.TERMINAL,
    'submit_outcome_unknown': Disposition.UNCERTAIN,
    'commit_outcome_unknown': Disposition.UNCERTAIN,
}


class WorkflowFailure(Exception):
    def __init__(self, code: str, message: str = '', *, evidence=None, effect=None):
        CODES[code]  # Unknown codes are programming errors, never fallback failures.
        if effect not in (None, 'not_committed', 'committed', 'unknown'):
            raise ValueError('Unknown failure effect')
        super().__init__(message)
        self.code = code
        self.evidence = dict(evidence or {})
        self.effect = effect

    @property
    def disposition(self) -> Disposition:
        return CODES[self.code]

    def record(self) -> dict:
        record = {'code': self.code, 'disposition': self.disposition.value,
                  'message': str(self), 'error_type': type(self).__name__,
                  'evidence': self.evidence}
        if self.effect is not None:
            record['effect'] = self.effect
        return record


def is_recoverable_provider_failure(diagnostic: dict) -> bool:
    code = diagnostic.get('code')
    allowed = code in ('provider_stream_error', 'provider_error')
    if code == 'provider_http_error':
        status = diagnostic.get('http_status')
        allowed = type(status) is int and (status in (408, 429) or 500 <= status <= 599)
    return allowed


def _http_code(status):
    if status == 408:
        return 'provider_timeout'
    if status == 429:
        return 'provider_rate_limited'
    if type(status) is int and 500 <= status <= 599:
        return 'provider_unavailable'
    return 'provider_rejected_request'


def classify(exc: BaseException) -> WorkflowFailure:
    if isinstance(exc, WorkflowFailure):
        return exc
    evidence = {'error_type': type(exc).__name__}
    effect = None
    if getattr(exc, 'uncertain', False):
        code = 'submit_outcome_unknown'
        effect = 'unknown'
    elif getattr(exc, 'retryable', False):
        code = 'runtime_not_adopted'
    elif isinstance(exc, urllib.error.HTTPError):
        code = _http_code(exc.code)
        evidence['http_status'] = exc.code
    elif isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
        code = 'provider_timeout'
    elif isinstance(exc, (urllib.error.URLError, ConnectionError, http.client.RemoteDisconnected)):
        code = 'transport_network'
    elif type(exc).__name__ == 'ModelResultError':
        diagnostic = getattr(exc, 'diagnostic', {}) or {}
        evidence['diagnostic'] = diagnostic
        diagnostic_code = diagnostic.get('code')
        if diagnostic_code == 'cloud_call_failed':
            code = 'cloud_job_failed'
            nested = (diagnostic.get('receipt') or {}).get('diagnostic') or {}
            evidence['recoverable'] = is_recoverable_provider_failure(nested)
        elif diagnostic_code == 'provider_http_error':
            code = _http_code(diagnostic.get('http_status'))
        elif diagnostic_code in ('incomplete_output', 'output_truncated'):
            code = 'output_truncated'
        elif diagnostic_code in ('provider_error', 'provider_stream_error'):
            code = 'provider_stream_error'
        else:
            code = 'model_output_invalid'
    elif isinstance(exc, OSError):
        code = 'local_io'
    elif isinstance(exc, (ValueError, KeyError)):
        code = 'contract_violation'
    elif isinstance(exc, (TypeError, AttributeError, ImportError, NameError)):
        code = 'code_defect'
    else:
        code = 'unclassified'
    return WorkflowFailure(code, str(exc), evidence=evidence, effect=effect)


def failure_of(record: Mapping) -> Disposition | None:
    failure = record.get('failure')
    if failure is None:
        return None
    return CODES[failure['code']]
