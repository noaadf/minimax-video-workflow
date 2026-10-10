"""The version declaration is not part of its own approved workflow fingerprint."""
from workflow.code_identity import workflow_fingerprint


def test_version_declaration_is_excluded_from_workflow_fingerprint(tmp_path):
    (tmp_path / 'module.py').write_text('value = 1\n')
    declaration = tmp_path / 'code_identity.py'
    declaration.write_text("SEMANTIC_VERSION = 'before'\n")
    before = workflow_fingerprint(tmp_path)
    declaration.write_text("SEMANTIC_VERSION = 'after'\n")
    assert workflow_fingerprint(tmp_path) == before
    (tmp_path / 'module.py').write_text('value = 2\n')
    assert workflow_fingerprint(tmp_path) != before
