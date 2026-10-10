"""Versioned code identity; formatting, docstrings and import locations are neutral.

The workflow scope deliberately remains the immediate workflow/*.py files used
by the original workflow_code_sha256. Assets and provider request digests keep
their existing byte identities. The approved AST is pinned in the test fixture;
changing it requires a new semantic version entry, even for helper extraction.
"""
import ast
from pathlib import Path

from .core.io import digest

SEMANTIC_VERSION = 'workflow-v2.47'


class _SemanticAST(ast.NodeTransformer):
    def visit_Import(self, node):
        return None

    def visit_ImportFrom(self, node):
        return None

    def generic_visit(self, node):
        # Strip only actual docstrings, never ordinary string expressions/data.
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:]
        return super().generic_visit(node)


def ast_fingerprint(source):
    tree = _SemanticAST().visit(ast.parse(source))
    return digest(ast.dump(tree, annotate_fields=True, include_attributes=False))


def workflow_fingerprint(root=None):
    root = Path(root) if root is not None else Path(__file__).parent
    return digest({p.name: ast_fingerprint(p.read_text(encoding='utf-8')) for p in sorted(root.glob('*.py'))
                   if p.name != 'code_identity.py'})


def current_version():
    """Read the declaration on disk so a stale process cannot hide a version bump."""
    for node in ast.parse(Path(__file__).read_text(encoding='utf-8')).body:
        if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SEMANTIC_VERSION'
                                               for t in node.targets)):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and value:
                return value
    raise ValueError('SEMANTIC_VERSION must declare a nonempty string')


def code_identity(path, *, version=None):
    return digest({'semantic_version': current_version() if version is None else version,
                   'ast_sha256': ast_fingerprint(Path(path).read_text(encoding='utf-8'))})


def workflow_code_sha256(root=None, *, version=None):
    return digest({'semantic_version': current_version() if version is None else version,
                   'ast_sha256': workflow_fingerprint(root)})
