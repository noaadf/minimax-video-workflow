import unittest
from workflow.codex_client import failure_categories


class DiagnosticTests(unittest.TestCase):
    def test_uuid_digits_are_not_http_codes(self):
        self.assertEqual(failure_categories('unexpected status 503 Service Unavailable; request id: 123403-401-429-504'), ['503'])

    def test_explicit_status_forms(self):
        for message in ('HTTP 401 Unauthorized', 'HTTP/1.1 401 Unauthorized', 'status code: 401'):
            self.assertEqual(failure_categories(message), ['401'])

    def test_non_http_failure(self):
        self.assertEqual(failure_categories('stream disconnected; timeout; request id: 403'), ['stream disconnected', 'timeout'])
