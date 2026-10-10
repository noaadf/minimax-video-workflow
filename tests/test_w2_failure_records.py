from workflow.core.failures import WorkflowFailure
from workflow.core.io import write_json
from workflow.review_failure import from_record
from workflow.storyboard_batch import transient_failure


def test_legacy_names_and_reason_strings_never_enable_retry(tmp_path):
    for name in ('TimeoutError', 'URLError', 'ConnectionError', 'RuntimeError'):
        record = {'status': 'failed', 'error_type': name,
                  'reason': 'review API unavailable or invalid response'}
        assert from_record(record) == (None, None)
        write_json(tmp_path / 'keyframe_review.json', record)
        detail = {}
        assert not transient_failure(tmp_path, classification=detail)
        assert detail == {}


def test_explicit_code_overrides_misleading_legacy_text(tmp_path):
    record = {'status': 'failed', 'error_type': 'TimeoutError',
              'failure': WorkflowFailure('config_invalid').record()}
    write_json(tmp_path / 'keyframe_review.json', record)
    detail = {}
    assert not transient_failure(tmp_path, classification=detail)
    assert detail['failure']['code'] == 'config_invalid'
    assert detail['classification_source'] == 'failure'
