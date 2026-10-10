"""Explicit, ordered manifest revisions with immutable output evidence."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path

from .core.io import digest, sha256, write_json
from .plan import validate


@dataclass(frozen=True)
class RevisionSpec:
    product_folder: Path
    attempt: int
    parent: str
    job_id_from: str
    job_id_to: str
    authority: str
    diagnosis: str
    actual_changes: str
    ops: tuple[dict, ...]

    def __post_init__(self):
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError('Positive attempt required')
        if self.authority not in ('owner_directive', 'diagnosed_review'):
            raise ValueError('Explicit revision authority required')
        if not self.parent or Path(self.parent).name != self.parent or self.parent in ('.', '..'):
            raise ValueError('Parent must be a manifest folder name')
        if not self.job_id_from or not self.job_id_to or self.job_id_from == self.job_id_to:
            raise ValueError('Distinct nonempty job identities required')
        if not isinstance(self.ops, tuple) or any(not isinstance(op, dict) for op in self.ops):
            raise ValueError('Ordered operation objects required')


def load_spec(path: Path) -> RevisionSpec:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data['product_folder'] = Path(data['product_folder'])
    data['ops'] = tuple(data['ops'])
    return RevisionSpec(**data)


def _shot(plan, sid):
    matches = [s for s in plan.get('shots', []) if s.get('id') == sid]
    if len(matches) != 1:
        raise ValueError('Exactly one shot required: ' + str(sid))
    return matches[0]


def _targets(plan, target):
    if target == 'top':
        return [plan]
    if target == 'shots:*':
        if not plan.get('shots'):
            raise ValueError('Nonempty shots required')
        return plan['shots']
    if target.startswith('shot:'):
        return [_shot(plan, target.split(':', 1)[1])]
    if target.startswith('narration:'):
        index = target.split(':', 1)[1]
        if not index.isdigit() or int(index) >= len(plan.get('narration', [])):
            raise ValueError('Narration index outside manifest')
        return [plan['narration'][int(index)]]
    raise ValueError('Unknown revision target: ' + str(target))


def _prompt_edit(plan, op, folder):
    shot = _shot(plan, op['shot'])
    count = op.get('count', 1)
    old = op['old']
    if (not isinstance(old, str) or not old or type(count) is not int or count < 1
            or not isinstance(op['new'], str) or shot.get('prompt', '').count(old) != count):
        raise ValueError('Prompt edit occurrence count differs')
    shot['prompt'] = shot['prompt'].replace(old, op['new'])


def _drop(plan, op, folder):
    targets = _targets(plan, op['target'])
    keys = op['keys']
    if not isinstance(keys, list) or not keys or any(not isinstance(key, str) for key in keys):
        raise ValueError('Nonempty drop keys required')
    # Optional keys mirror the legacy cleanup operations; target existence is strict.
    for target in targets:
        for key in keys:
            target.pop(key, None)


def _copy_from(plan, op, folder):
    name = op['manifest']
    if not name or Path(name).name != name or name in ('.', '..'):
        raise ValueError('Copy source must be an explicit manifest folder')
    source = folder / name / 'manifest.json'
    if not source.is_file():
        raise ValueError('Copy source manifest missing: ' + str(source))
    source_shot = _shot(json.loads(source.read_text(encoding="utf-8")), op['shot'])
    fields = op['fields']
    if not isinstance(fields, list) or not fields or any(field not in source_shot for field in fields):
        raise ValueError('Copy source fields missing')
    _shot(plan, op['shot']).update({field: source_shot[field] for field in fields})


def _reuse_video(plan, op, folder):
    path = Path(op['path'])
    if not path.is_absolute() or not path.is_file():
        raise ValueError('Explicit existing absolute video path required')
    shot = _shot(plan, op['shot'])
    shot.update(source_path=str(path), source_start=0, previsual_path=str(path))
    for key in ('reference_images', 'keyframe_prompts'):
        shot.pop(key, None)


def _set(plan, op, folder):
    targets = _targets(plan, op['target'])
    if not isinstance(op['key'], str) or not op['key']:
        raise ValueError('Nonempty set key required')
    for target in targets:
        target[op['key']] = op['value']


OPS = {'prompt_edit': _prompt_edit, 'drop': _drop, 'copy_from': _copy_from,
       'reuse_video': _reuse_video, 'set': _set}


def _spec_record(spec):
    return {**spec.__dict__, 'product_folder': str(spec.product_folder), 'ops': list(spec.ops)}


def derive(spec: RevisionSpec, *, output: Path | None = None) -> dict:
    """An output override permits read-only migration checks against live parents."""
    folder = Path(spec.product_folder)
    source = folder / spec.parent / 'manifest.json'
    if not source.is_file():
        raise ValueError('Parent manifest missing: ' + str(source))
    plan = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(plan.get('id'), str) or plan['id'].count(spec.job_id_from) != 1:
        raise ValueError('Parent job_id_from must occur exactly once')
    plan['id'] = plan['id'].replace(spec.job_id_from, spec.job_id_to)
    # JSON-copy ops so mutable values never bind the spec to the derived plan.
    ops = json.loads(json.dumps(list(spec.ops), ensure_ascii=False))
    reused = {}
    for op in ops:
        name = op.get('op')
        if name not in OPS:
            raise ValueError('Unknown revision operation: ' + str(name))
        try:
            OPS[name](plan, op, folder)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError('Malformed revision operation: ' + str(name)) from exc
        if name == 'reuse_video':
            reused[op['shot']] = {'path': op['path'], 'sha256': sha256(op['path'])}
    validate(plan)
    dest = Path(output) if output is not None else folder / f'manifest_story_rework_r{spec.attempt}'
    manifest = dest / 'manifest.json'
    receipt = {'attempt': spec.attempt, 'parent_manifest_sha256': sha256(source),
               'diagnosis': spec.diagnosis, 'actual_changes': spec.actual_changes,
               'ops': ops, 'spec_sha256': digest(_spec_record(spec)), 'authority': spec.authority,
               'reused_videos': reused}
    receipt_path = dest / 'diagnosed_repair.json'
    if manifest.exists():
        if digest(json.loads(manifest.read_text(encoding="utf-8"))) != digest(plan):
            raise ValueError('Existing revision manifest conflicts with spec')
        receipt['manifest_sha256'] = sha256(manifest)
        if not receipt_path.is_file() or json.loads(receipt_path.read_text(encoding="utf-8")) != receipt:
            raise ValueError('Existing revision receipt conflicts with spec')
        return receipt
    if dest.exists() and any(dest.iterdir()):
        raise ValueError('Revision output is nonempty without a manifest')
    write_json(manifest, plan)
    receipt['manifest_sha256'] = sha256(manifest)
    write_json(receipt_path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(json.dumps(derive(load_spec(args.spec), output=args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
