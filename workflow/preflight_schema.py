"""Declarative, local-only gates for compiled manifests and stage inputs.

Authored manifest fields exist at compile time. Task canvas/lease metadata is
created on claim; audio/video results exist only when ready. Never infer runtime
results from an authored manifest or require them before their declared time.
This module imports dependencies for diagnostics only: no provider or queue work.
"""
from dataclasses import dataclass
import importlib
import math
from pathlib import Path
import sys
from typing import Mapping

PROJECT = Path(__file__).resolve().parents[1]
EXPECTED_PREFIX = PROJECT / '.venv-workflow'
DEPENDENCIES = ('opencc', 'pypinyin', 'edge_tts', 'PIL', 'numpy')
TIMINGS = ('compiled', 'before_claim', 'claimed', 'ready')


@dataclass(frozen=True)
class Field:
    path: str
    kind: str = 'text'
    available_at: str = 'compiled'


@dataclass(frozen=True)
class StageSchema:
    manifest: tuple = ()
    task: tuple = ()
    context: tuple = ()


@dataclass(frozen=True)
class PreflightResult:
    stage: str
    errors: tuple = ()
    deferred: tuple = ()

    @property
    def ok(self):
        return not self.errors

    def require(self):
        if self.errors:
            raise ValueError('Preflight ' + self.stage + ':\n- ' + '\n- '.join(self.errors))
        return self


BASE = (
    Field('schema_version', 'version'), Field('id'), Field('title'),
    Field('cover_title'), Field('template'), Field('duration_seconds', 'positive'),
    Field('aspect_ratio'), Field('continuity_bible'), Field('comment_prompt'),
    Field('facts', 'list'), Field('facts.*.id'), Field('facts.*.text'),
    Field('facts.*.source_url'), Field('shots', 'list'), Field('shots.*.id'),
    Field('shots.*.camera'), Field('shots.*.prompt'),
    Field('shots.*.duration_seconds', 'positive'), Field('narration', 'list'),
    Field('narration.*.start', 'nonnegative'), Field('narration.*.end', 'positive'),
    Field('narration.*.text'), Field('music.path'), Field('music.license_note'),
)
STORYBOARD = (
    Field('caption_policy'), Field('story_contract', 'object'),
    *(Field('story_contract.' + name) for name in (
        'central_question', 'story_one_sentence', 'protagonist', 'goal', 'obstacle', 'resolution')),
    Field('story_contract.beats', 'list'), Field('story_contract.beats.*.id'),
    Field('story_contract.beats.*.caused_by', 'nullable_text'),
    Field('story_contract.beats.*.action'), Field('story_contract.beats.*.result'),
    *(Field('shots.*.storyboard.' + name) for name in (
        'beat_id', 'scene_id', 'purpose', 'attention_target', 'action', 'result', 'cut_reason')),
    Field('shots.*.storyboard.state_before', 'object'),
    Field('shots.*.storyboard.state_after', 'object'),
    Field('shots.*.storyboard.transition.kind'),
    Field('narration.*.caption_lines', 'list'),
)
NARRATIVE = (
    Field('narrative_contract.central_question'), Field('narrative_contract.story_one_sentence'),
    Field('narrative_contract.beats', 'list'), Field('narrative_contract.beats.*.id'),
    Field('narrative_contract.beats.*.causal_role'), Field('narrative_contract.beats.*.claim'),
    Field('narrative_contract.beats.*.fact_ids', 'array'),
    Field('narrative_contract.shots', 'list'), Field('narrative_contract.shots.*.id'),
    Field('narrative_contract.shots.*.narration_index', 'nonnegative_integer'),
    *(Field('narrative_contract.shots.*.' + name) for name in (
        'narration', 'beat_id', 'relation_to_previous', 'visible_action', 'visual_constraint')),
    Field('narrative_contract.shots.*.fact_ids', 'array'),
    *(Field('narrative_contract.ending.' + name) for name in ('result', 'tradeoff', 'comment_prompt')),
)

# Paths in task/context are deliberately separate from manifest paths.
SCHEMAS = {
    'narrative_review': StageSchema(manifest=BASE),
    'image': StageSchema(manifest=BASE, task=(
        Field('aspect_ratio', available_at='claimed'),
        Field('target_dimensions.width', 'positive_integer', 'claimed'),
        Field('target_dimensions.height', 'positive_integer', 'claimed'),
        Field('claim_token', available_at='claimed'),
    )),
    'video': StageSchema(manifest=BASE),
    'speech': StageSchema(manifest=BASE, context=(Field('narration_path', 'path', 'before_claim'),)),
    'music': StageSchema(manifest=BASE, context=(Field('music_path', 'path', 'before_claim'),)),
    'compose': StageSchema(manifest=BASE, context=(
        Field('voice.audio_path', 'path', 'ready'),
        Field('voice.effective_narration', 'list', 'ready'),
        Field('bed.audio_path', 'path', 'ready'),
    )),
    'quality': StageSchema(manifest=BASE, context=(
        Field('compose.video_path', 'path', 'ready'),
        Field('compose.manifest_path', 'path', 'ready'),
        Field('compose.contact_path', 'path', 'ready'),
        Field('bed.audio_path', 'path', 'ready'),
    )),
    'export': StageSchema(manifest=BASE, context=(
        *(Field('compose.' + name, 'path', 'ready') for name in (
            'video_path', 'cover_path', 'contact_path', 'manifest_path')),
        Field('quality.passed', 'true', 'ready'), Field('quality.editorial_status', available_at='ready'),
    )),
}


def validate_runtime():
    """Import actual modules, catching broken installs as well as missing modules."""
    errors = []
    if Path(sys.prefix).resolve() != EXPECTED_PREFIX.resolve():
        errors.append(f'environment.python_prefix: expected {EXPECTED_PREFIX}, got {sys.prefix}; '
                      'use .venv-workflow/bin/python')
    for name in DEPENDENCIES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            errors.append(f'environment.dependencies.{name}: {type(exc).__name__}: {exc}')
    return PreflightResult('environment', tuple(errors))


_MISSING = object()


def _values(value, parts, prefix=''):
    if not parts:
        yield prefix, value
        return
    key, *rest = parts
    if key == '*':
        if isinstance(value, list):
            for index, child in enumerate(value):
                yield from _values(child, rest, f'{prefix}[{index}]')
        return  # Container type/emptiness is reported by its own field.
    child = value.get(key, _MISSING) if isinstance(value, Mapping) else _MISSING
    yield from _values(child, rest, f'{prefix}.{key}' if prefix else key)


def _valid(value, kind):
    if value is _MISSING:
        return False
    if kind == 'nullable_text':
        return value is None or isinstance(value, str) and bool(value.strip())
    if kind == 'text':
        return isinstance(value, str) and bool(value.strip())
    if kind == 'path':
        return isinstance(value, (str, Path)) and bool(str(value).strip())
    if kind in ('list', 'array'):
        return isinstance(value, list) and (kind == 'array' or bool(value))
    if kind == 'object':
        return isinstance(value, Mapping) and bool(value)
    if kind == 'version':
        return type(value) is int and value == 1
    if kind == 'true':
        return value is True
    if kind in ('positive_integer', 'nonnegative_integer'):
        return type(value) is int and value >= (1 if kind == 'positive_integer' else 0)
    if kind in ('positive', 'nonnegative'):
        return type(value) in (int, float) and math.isfinite(value) and (
            value > 0 if kind == 'positive' else value >= 0)
    raise ValueError('Unknown schema field type: ' + kind)


def validate_manifest(manifest, stage, *, timing='compiled', task=None,
                      context=None, required_reads=()):
    """Collect every missing/type/timing error, including runtime diagnostics.

    ``compile`` checks all eight stages' authored requirements once. Optional
    required_reads uses qualified paths (e.g. task.aspect_ratio) to diagnose an
    adapter attempting to access a field before it is generated, without reading
    that field. Result paths are checked only at their declared availability.
    """
    errors = list(validate_runtime().errors)
    deferred = []
    stages = tuple(SCHEMAS) if stage == 'compile' else (stage,)
    if timing not in TIMINGS:
        errors.append('timing: unknown ' + str(timing))
    if any(name not in SCHEMAS for name in stages):
        errors.append('stage: unknown ' + str(stage))
    if not isinstance(manifest, Mapping):
        errors.append('manifest: expected object')
    if errors and (timing not in TIMINGS or any(name not in SCHEMAS for name in stages)):
        return PreflightResult(stage, tuple(errors))
    manifest = manifest if isinstance(manifest, Mapping) else {}
    declared = []
    if manifest.get('pacing_policy') == 'storyboard_v1':
        declared.extend(('manifest', field) for field in STORYBOARD)
    elif 'narrative_review' in stages:
        declared.extend(('manifest', field) for field in NARRATIVE)
    if manifest.get('pacing_policy') in ('storyboard_v1', 'editorial_v2', 'narrative_timed_v1'):
        declared.append(('manifest', Field('shots.*.duration_reason')))
    for name in stages:
        schema = SCHEMAS[name]
        for scope in ('manifest', 'task', 'context'):
            declared.extend((scope, field) for field in getattr(schema, scope))
    inputs = {'manifest': manifest, 'task': task, 'context': context}
    for scope, field in dict.fromkeys(declared):
        qualified = scope + '.' + field.path
        if TIMINGS.index(timing) < TIMINGS.index(field.available_at):
            deferred.append(qualified + ': generated at ' + field.available_at)
            if qualified in required_reads:
                errors.append(qualified + ': timing violation; available at ' + field.available_at +
                              ', cannot read at ' + timing)
            continue
        for path, value in _values(inputs[scope], field.path.split('.')):
            if not _valid(value, field.kind):
                errors.append(scope + '.' + path + ': ' +
                              ('missing' if value is _MISSING or value is None else 'invalid') +
                              '; expected ' + field.kind)
            elif scope == 'context' and field.kind == 'path' and timing == 'ready':
                if not Path(value).is_file():
                    errors.append(scope + '.' + path + ': result file missing: ' + str(value))
    # Validate authored canvas directly, never a pending task's derived canvas.
    if _valid(manifest.get('aspect_ratio'), 'text'):
        try:
            from .storyboard_image_jobs import _canvas
            _canvas(manifest)
        except Exception as exc:
            errors.append('manifest.canvas: ' + str(exc))
    return PreflightResult(stage, tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(deferred)))


def require_compiled_manifest(manifest):
    """Schema first (all field errors), then the existing semantic contract."""
    validate_manifest(manifest, 'compile').require()
    from .plan import validate
    return validate(manifest)
