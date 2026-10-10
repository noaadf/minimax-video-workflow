"""One code table across exception, JSON and legacy review boundaries."""
import http.client
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from workflow.anthropic_client import ModelResultError
from workflow.core.failures import (CODES, Disposition, WorkflowFailure, classify,
                                    failure_of, is_recoverable_provider_failure)
from workflow.core.io import write_json
from workflow.review import editorial_review
from workflow.storyboard_batch import transient_failure


class FailureContractTests(unittest.TestCase):
    def test_table(self):
        rows = [(TimeoutError(), 'provider_timeout'),
                (subprocess.TimeoutExpired('fake', 1), 'provider_timeout'),
                (urllib.error.URLError('offline'), 'transport_network'),
                (ConnectionError(), 'transport_network'),
                (http.client.RemoteDisconnected(), 'transport_network'),
                (OSError(), 'local_io'), (ValueError(), 'contract_violation'),
                (KeyError(), 'contract_violation'), (TypeError(), 'code_defect'),
                (AttributeError(), 'code_defect'), (ImportError(), 'code_defect'),
                (NameError(), 'code_defect'), (RuntimeError(), 'unclassified'),
                (Exception(), 'unclassified')]
        for status in (408, 429, 500, 503, 599, 400, 401, 403, 404, 413, 422, 302):
            code = ('provider_timeout' if status == 408 else
                    'provider_rate_limited' if status == 429 else
                    'provider_unavailable' if 500 <= status <= 599 else
                    'provider_rejected_request')
            rows.append((urllib.error.HTTPError('https://invalid', status, 'fake', {}, None), code))
        for exc, code in rows:
            with self.subTest(exc=type(exc).__name__, code=code):
                failure = classify(exc)
                self.assertEqual(failure.code, code)
                record = json.loads(json.dumps({'failure': failure.record()}))
                self.assertEqual(failure_of(record), CODES[code])
        failure = WorkflowFailure('config_invalid', 'bad', evidence={'field': 'transport'})
        self.assertIs(classify(failure), failure)
        self.assertIsNone(failure_of({}))
        with self.assertRaises(KeyError): WorkflowFailure('typo')
        with self.assertRaises(KeyError): failure_of({'failure': {'code': 'typo'}})

    def test_stage_attributes_and_effect(self):
        for uncertain, retryable, code in ((True, True, 'submit_outcome_unknown'),
                                          (False, True, 'runtime_not_adopted'),
                                          (False, False, 'unclassified')):
            exc = RuntimeError('stage'); exc.uncertain = uncertain; exc.retryable = retryable
            self.assertEqual(classify(exc).code, code)
        for effect in ('not_committed', 'committed', 'unknown'):
            self.assertEqual(WorkflowFailure('local_io', effect=effect).record()['effect'], effect)
        with self.assertRaises(ValueError): WorkflowFailure('local_io', effect='invalid')

    def test_model_diagnostics_and_recovery_semantics(self):
        for diagnostic, code in (('incomplete_output', 'output_truncated'),
                                 ('provider_stream_error', 'provider_stream_error'),
                                 ('invalid_json', 'model_output_invalid'),
                                 ('non_object_json', 'model_output_invalid')):
            self.assertEqual(classify(ModelResultError(diagnostic, {})).code, code)
        for status in (408, 429, 500, 599, 400, '503', True, None):
            diagnostic = {'code': 'provider_http_error', 'http_status': status}
            failure = classify(ModelResultError('cloud_call_failed', {'diagnostic': diagnostic}))
            self.assertEqual(failure.code, 'cloud_job_failed')
            self.assertEqual(failure.evidence['recoverable'],
                             type(status) is int and (status in (408, 429) or 500 <= status <= 599))
        for code in ('provider_stream_error', 'provider_error', 'invalid_json', 'incomplete_output'):
            self.assertEqual(is_recoverable_provider_failure({'code': code}),
                             code in ('provider_stream_error', 'provider_error'))
        from workflow.cloud_llm import request_identity
        from workflow.cloud_llm_client import recovery_request
        request = {'instruction': 'fake', 'data': {}, 'model': 'glm-5.3', 'max_tokens': 1, 'images': []}
        for diagnostic in ({'code': 'provider_error'}, {'code': 'provider_stream_error'},
                           {'code': 'provider_http_error', 'http_status': 503},
                           {'code': 'provider_http_error', 'http_status': 400}):
            prior = {'id': request_identity(request), 'state': 'failed', 'result': None,
                     'diagnostic': diagnostic}
            if is_recoverable_provider_failure(diagnostic):
                self.assertIn('transport_attempt', recovery_request(request, prior))
            else:
                with self.assertRaises(ValueError): recovery_request(request, prior)

    def review_roundtrip(self, exc):
        temp_root = Path(__file__).resolve().parents[1] / 'tmp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as d:
            root = Path(d); contact = root / 'contact.jpg'; contact.write_bytes(b'fake')
            config = {'model': 'fake', 'provider': 'fake', 'base_url': 'https://invalid', 'transport': 'responses'}
            with patch('workflow.review.resolve', return_value=config), \
                 patch('workflow.responses_client.ask', side_effect=exc) as ask, \
                 patch.dict('os.environ', {'WORKFLOW_REVIEW_CACHE': str(root / 'cache')}):
                result = editorial_review({'review_mode': 'api', 'shots': [], 'narration': []}, contact, stage='preflight')
            self.assertEqual(ask.call_count, 1)
            write_json(root / 'keyframe_review.json', result)
            loaded = json.loads((root / 'keyframe_review.json').read_text())
            # Frozen review preserves the dispatcher diagnostic; consumer uses its code.
            write_json(root / 'contract.json', {'failure': classify(exc).record()})
            expected = failure_of(json.loads((root / 'contract.json').read_text())) == Disposition.RETRY
            self.assertEqual(loaded['error_type'], type(exc).__name__)
            self.assertEqual(transient_failure(root), expected)

    def test_provider_503_and_network_producer_consumer(self):
        for exc in (urllib.error.HTTPError('https://invalid', 503, 'fake', {}, None),
                    urllib.error.URLError('offline')):
            self.review_roundtrip(exc)

    def test_D1_http_400_is_terminal(self):
        self.review_roundtrip(urllib.error.HTTPError('https://invalid', 400, 'fake', {}, None))

    def test_D1_config_key_error_is_terminal(self):
        self.review_roundtrip(KeyError('model'))

    def test_D2_bare_runtime_error_is_terminal(self):
        with tempfile.TemporaryDirectory() as d:
            write_json(Path(d) / 'story_review.json', {'status': 'failed', 'error_type': 'RuntimeError'})
            self.assertEqual(transient_failure(Path(d)), classify(RuntimeError()).disposition == Disposition.RETRY)
