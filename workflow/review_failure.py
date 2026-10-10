"""Read structured failure evidence without legacy exception-name guessing."""
from .core.failures import WorkflowFailure, classify

def from_record(record):
    """Return (failure, provenance); content verdicts have no failure."""
    diagnostic = record.get('diagnostic') or {}
    structured = record.get('failure') or diagnostic.get('failure')
    if structured:
        return WorkflowFailure(structured['code'], structured.get('message', ''),
                               evidence=structured.get('evidence'),
                               effect=structured.get('effect')), 'failure'
    if diagnostic.get('code'):
        from .anthropic_client import ModelResultError
        error = ModelResultError(diagnostic['code'], diagnostic.get('receipt') or {})
        error.diagnostic = diagnostic
        return classify(error), 'diagnostic'
    return None, None


def annotate(record):
    """Add failure metadata only; never change the reason or review decision."""
    failure, source = from_record(record)
    if failure:
        return {**record, 'failure': failure.record(), 'classification_source': source}
    return record
