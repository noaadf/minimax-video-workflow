import pytest

from workflow.material_pipeline.core import _validate_publication_date


def test_arxiv_visible_version_date_keeps_original_body():
    body = 'arXiv:2610.03695v1 [cs.CL] 02 Oct 2026'
    _validate_publication_date({'published': '2026-10-02', 'date_evidence': body}, body)


@pytest.mark.parametrize('date,evidence,body', [
    ('2026-10-03', '02 Oct 2026', '02 Oct 2026'),
    ('2026-02-28', '31 Feb 2026', '31 Feb 2026'),
    ('2026-10-02', '02 Oct 2026', 'undated article'),
])
def test_day_month_evidence_still_rejects_wrong_or_missing_dates(date, evidence, body):
    with pytest.raises(ValueError, match='publication date'):
        _validate_publication_date({'published': date, 'date_evidence': evidence}, body)
