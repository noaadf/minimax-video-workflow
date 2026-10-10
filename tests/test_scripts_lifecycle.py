"""Every active script has a real static caller or an explicit LIVE rationale."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '.venv-workflow', 'archives', 'output', 'tmp', '__pycache__',
            '.pytest_cache', '.agents', '.codex', 'node_modules'}
AUDIT_FILES = {'refactor_scripts_audit_20261006.json', 'refactor_W0_report_20261006.md',
               'refactor_W1_archive_audit_20261006.json', 'refactor_W1_report_20261006.md',
               'refactor_W1_commit_plan_20261006.json', 'refactor_W1_moves_20261006.json',
               'LIVE.txt', 'test_scripts_lifecycle.py'}


def corpus():
    # Ignore audit inventories: listing a filename is not an active reference.
    for top in ROOT.iterdir():
        if top.name in EXCLUDED or top.name.startswith('.venv') or top.is_symlink():
            continue
        paths = top.rglob('*') if top.is_dir() else [top]
        for path in paths:
            if (not path.is_file() or path.is_symlink() or path.name in AUDIT_FILES
                    or set(path.relative_to(ROOT).parts) & EXCLUDED):
                continue
            try:
                raw = path.read_bytes()
                if b'\0' not in raw:
                    yield path, raw.decode('utf-8')
            except (UnicodeError, OSError):
                continue


def live_entries():
    entries = set()
    for line in (ROOT / 'scripts/LIVE.txt').read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        path, reason = line.split('|', 1)
        assert reason.strip(), 'LIVE entries require an evidence/uncertainty rationale'
        entries.add(path.strip())
    return entries


def test_top_level_scripts_have_reference_or_live_rationale():
    texts = list(corpus()); live = live_entries(); missing = []
    for path in sorted((ROOT / 'scripts').iterdir()):
        if not path.is_file() or path.name == 'LIVE.txt':
            continue
        names = {path.name, path.stem} if path.suffix == '.py' else {path.name}
        pattern = re.compile(r'(?<![\w])(?:' + '|'.join(re.escape(n) for n in names) + r')(?![\w])')
        referenced = any(other != path and pattern.search(text) for other, text in texts)
        if not referenced and path.name not in live:
            missing.append(path.name)
    assert not missing, 'Unregistered scripts: ' + ', '.join(missing)


def test_live_registry_paths_exist():
    for rel in live_entries():
        path = ROOT / 'scripts' / rel
        assert path.is_file()
        assert path.resolve().is_relative_to((ROOT / 'scripts').resolve())
