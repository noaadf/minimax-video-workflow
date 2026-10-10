import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_CYCLE_MODULES = 25  # W1 measured ratchet (W0: 37); never raise to fit a change.
SCRIPT_IMPORT_ALLOWLIST = set()  # W1: no library-to-driver imports.


def module_name(path):
    parts = path.relative_to(ROOT).with_suffix('').parts
    return '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)


def import_graph():
    modules = {module_name(p): p for p in (ROOT / 'workflow').rglob('*.py')}
    graph = {name: set() for name in modules}
    script_imports = set()
    for name, path in modules.items():
        package = name if path.name == '__init__.py' else name.rpartition('.')[0]
        for node in ast.walk(ast.parse(path.read_text())):
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = package.split('.')
                    base = '.'.join(parts[:len(parts) - node.level + 1])
                    base += ('.' + node.module) if node.module else ''
                else:
                    base = node.module or ''
                targets = [base] + [base + '.' + a.name for a in node.names]
                if base == 'scripts' or base.startswith('scripts.'):
                    script_imports.add((name, base, tuple(a.name for a in node.names)))
            if isinstance(node, ast.Import):
                for target in targets:
                    if target == 'scripts' or target.startswith('scripts.'):
                        script_imports.add((name, target, ()))
            graph[name].update(target for target in targets if target in modules)
    return graph, script_imports


def largest_cycle(graph):
    # Tarjan includes function-local imports: delayed imports still couple modules.
    index = {}; low = {}; stack = []; active = set(); sizes = []
    def visit(node):
        index[node] = low[node] = len(index)
        stack.append(node); active.add(node)
        for child in graph[node]:
            if child not in index:
                visit(child); low[node] = min(low[node], low[child])
            elif child in active:
                low[node] = min(low[node], index[child])
        if low[node] == index[node]:
            size = 0
            while True:
                child = stack.pop(); active.remove(child); size += 1
                if child == node:
                    break
            sizes.append(size)
    for node in graph:
        if node not in index:
            visit(node)
    return max(sizes, default=0)


def test_import_ratchet():
    graph, scripts = import_graph()
    assert largest_cycle(graph) <= MAX_CYCLE_MODULES
    assert scripts <= SCRIPT_IMPORT_ALLOWLIST
    for name, targets in graph.items():
        if name == 'workflow.core' or name.startswith('workflow.core.'):
            assert not {t for t in targets if t.startswith('workflow.')}


def test_core_standard_library_only():
    import sys
    for path in (ROOT / 'workflow/core').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert all(a.name.split('.')[0] in sys.stdlib_module_names for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0
                assert (node.module or '').split('.')[0] in sys.stdlib_module_names


def test_plan_io_reexports():
    from workflow import plan
    from workflow.core import io
    assert plan.write_json is io.write_json
    assert plan.digest is io.digest
    assert plan.sha256 is io.sha256
