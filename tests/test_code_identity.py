"""Identity laws and the explicit version/AST approval ratchet."""
import json
from pathlib import Path

import pytest

from workflow.code_identity import (SEMANTIC_VERSION, ast_fingerprint, code_identity,
                                    workflow_code_sha256, workflow_fingerprint)


def test_format_comments_docstrings_and_imports_are_identity_neutral(tmp_path):
    path = tmp_path / 'module.py'
    path.write_text('"""Old docs."""\nimport os\ndef f(x):\n    """Doc."""\n    return x + 1\n')
    identity = workflow_code_sha256(tmp_path)
    module = code_identity(path)
    path.write_text('# comment\n"""New docs."""\nfrom pathlib import Path\n\ndef f( x ):\n    """New doc."""\n    return (x+1)  # comment\n')
    assert workflow_code_sha256(tmp_path) == identity
    assert code_identity(path) == module


def test_ast_semantics_change_identity(tmp_path):
    path = tmp_path / 'module.py'
    path.write_text('value = 1\n')
    identity = workflow_code_sha256(tmp_path)
    module = code_identity(path)
    path.write_text('value = 2\n')
    assert workflow_code_sha256(tmp_path) != identity
    assert code_identity(path) != module


def test_explicit_version_bump_changes_identity(tmp_path):
    path = tmp_path / 'module.py'
    path.write_text('value = 1\n')
    assert workflow_code_sha256(tmp_path, version='v1') != workflow_code_sha256(tmp_path, version='v2')
    assert code_identity(path, version='v1') != code_identity(path, version='v2')


def test_loaded_process_detects_version_only_bump(tmp_path, monkeypatch):
    from workflow import code_identity as module
    declaration = tmp_path / 'identity.py'
    declaration.write_text("SEMANTIC_VERSION = 'before'\n")
    source = tmp_path / 'source.py'; source.write_text('value = 1\n')
    monkeypatch.setattr(module, '__file__', str(declaration))
    loaded = module.code_identity(source)
    declaration.write_text("SEMANTIC_VERSION = 'after'\n")
    assert module.code_identity(source) != loaded


def test_exact_original_top_level_scope(tmp_path):
    (tmp_path / 'one.py').write_text('value = 1\n')
    identity = workflow_code_sha256(tmp_path)
    nested = tmp_path / 'core'; nested.mkdir()
    (nested / 'helper.py').write_text('value = 2\n')
    assert workflow_code_sha256(tmp_path) == identity
    (tmp_path / 'two.py').write_text('value = 2\n')
    assert workflow_code_sha256(tmp_path) != identity


def test_strings_and_invalid_syntax_are_not_silently_ignored():
    assert ast_fingerprint('value="one"') != ast_fingerprint('value="two"')
    with pytest.raises(SyntaxError):
        ast_fingerprint('def broken(')


def test_ast_change_requires_new_approved_semantic_version():
    approved = json.loads((Path(__file__).parent / 'fixtures/workflow_code_versions.json').read_text())
    assert workflow_fingerprint() == approved[SEMANTIC_VERSION], 'AST changed: bump SEMANTIC_VERSION and pin its new fingerprint'
