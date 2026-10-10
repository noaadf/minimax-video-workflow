"""Thin adapter: production plan (storyboard shots) -> film_ir compile -> backend request.

Single construction entry for plan['shot_pipeline'] == 'film_ir'. The legacy
constructor in runner.visual_generation_request stays frozen for 'legacy' plans;
'film_ir_shadow' submits the legacy request and records a field-by-field diff
against this compilation (the pre-switch safety net). No silent fallback: a
compile violation raises here instead of quietly using the legacy dict.

Parity rules (what the shadow diff must show empty before switching):
  prompt        visual_continuity + '\\n' + shot.prompt, verbatim ('legacy_verbatim'
                contract; compile_shot strips outer whitespace, a no-op on authored plans)
  duration      max(4, ceil(slot_seconds)) seconds == tier frames / 24
  generation    cfg generation steps/short_edge
  reference_images  plan refs in order: [0] start, [1] end (first_last when 2)
  task_id       plan_id-shot_id (worker transport identity; segment identity lives in the manifest)
"""
import math
from pathlib import Path

from . import film_ir
from .core.io import digest

PIPELINES = ('legacy', 'film_ir_shadow', 'film_ir')
PROMPT_CONTRACTS = ('legacy_verbatim', 'v3_binding', 'h3_fl2va_v1')
# Violations that must never reach a backend through this adapter (no fallback).
BLOCKING_VIOLATIONS = frozenset({'missing_start_kf', 'missing_kf_file', 'slot_infeasible',
                                 'missing_end_kf_blocked', 'kf_count_invalid', 'h3_fl2va_invalid'})
REFERENCE_SUFFIXES = frozenset({'.png', '.jpg', '.jpeg', '.webp'})
# Timebase law (MEASURED_NATIVE_FRAMES / native_frames_law / native_seconds)
# lives in film_ir.py — the single source of truth for duration/tier/mark
# conversions. This module only derives per-shot profiles from it.
from .film_ir import native_frames as _native_frames  # re-export point for callers


def pipeline_of(plan):
    mode = plan.get('shot_pipeline', 'legacy')
    if mode not in PIPELINES:
        raise ValueError(f'unknown shot_pipeline: {mode!r} (expected one of {PIPELINES})')
    return mode


def _is_generated(shot):
    return not shot.get('source_path') and shot.get('production_mode') != 'deterministic_graphic'




def _slot_start(plan, shot_id):
    """Timeline start of a shot: cumulative durations of every earlier shot.
    Reused/source shots advance the film timeline exactly like generated ones."""
    start = 0.0
    for s in plan['shots']:
        if s['id'] == shot_id:
            return start
        start += float(s['duration_seconds'])
    raise ValueError(f'shot {shot_id!r} not found in plan')


def _authored_prompt(plan, shot):
    prompt = shot['prompt']
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('shot visual prompt must be nonempty text')
    visual = plan.get('visual_continuity', '')
    if not isinstance(visual, str):
        raise ValueError('visual_continuity must be explicitly authored text')
    if visual.strip():
        if plan.get('film_ir_prompt_contract') == 'h3_fl2va_v1':
            # The official alignment line must be line 1; continuity belongs INSIDE the
            # three fields. Prepending would corrupt the structure (astra r1 P1).
            raise ValueError('h3_fl2va_v1 requires visual_continuity inside the prompt body; '
                             'extra prefixes are rejected')
        prompt = visual.strip() + '\n' + prompt
    return prompt


def _ir_shot(plan, shot, contract):
    raw = shot.get('reference_images', [])
    bad = [i for i, r in enumerate(raw) if not isinstance(r, str)]
    if bad:
        # A None/slip entry must not be reinterpreted as "no keyframe" (silent conditioning change).
        raise ValueError(f'{shot["id"]}: non-string reference_images entries at indices {bad}; '
                         'authoring slip — refusing to guess the intended conditioning')
    refs = list(raw)
    if len(refs) > 2:
        # Slicing to start+end would silently change the conditioning input (no silent fallback).
        raise ValueError(f'{shot["id"]}: {len(refs)} reference images; film_ir supports at most '
                         'start+end — author exactly the frames the backend should bind')
    contract = contract or 'legacy_verbatim'
    if contract not in PROMPT_CONTRACTS:
        raise ValueError(f'unknown film_ir_prompt_contract: {contract!r}')
    return {'id': shot['id'],
            'slot_start': _slot_start(plan, shot['id']),
            'slot_seconds': float(shot['duration_seconds']),
            'seed': shot.get('seed', 1),
            # The plan's ref list IS the authored conditioning: [start, end] -> first_last.
            'conditioning': 'first_last' if len(refs) >= 2 else 'first_only',
            'prompt_authored': _authored_prompt(plan, shot),
            'prompt_contract': contract,
            'keyframes': {'start': refs[:1], 'end': refs[1:2]}}


def _tier_profile(shot, cfg):
    """Duration tier mirrors the legacy request (max(4, ceil(slot)) seconds); the
    native frame count is the MEASURED backend count for that tier, not seconds*24.
    'seconds' is the request parameter; 'frames' is the expected artifact length."""
    seconds = max(4, math.ceil(float(shot['duration_seconds'])))
    generation = cfg.get('generation') or {'steps': 50, 'short_edge': 768}
    return {'name': f'h3-fl2va-{_native_frames(seconds)}f', 'seconds': seconds,
            'frames': _native_frames(seconds),
            'steps': generation.get('steps', 50), 'short_edge': generation.get('short_edge', 768)}


def _cfg_for(plan, shot):
    # Same expression as runner.ssh_generate: a present-but-empty entry must not
    # silently fall through to a different backend config.
    return plan.get('shot_backends', {}).get(shot['id'], plan.get('backend', {}))


def identity_restatement_problem(plan, shot):
    """Wardrobe-drift rule (open-h3-ir R15 analog): a subject stated once in the
    bible drifts across shots. A prompt that names a subject must restate its
    persistent identity tokens; authored plan field subject_identity =
    [{'id','name_tokens':[...],'must_restate':[...]}]. Warning-class, never blocking."""
    for subject in plan.get('subject_identity') or []:
        desc = shot.get('prompt') or ''
        names = subject.get('name_tokens') or []
        restated = subject.get('must_restate') or []
        if not names or not restated:
            continue
        if any(n in desc for n in names) and not any(t in desc for t in restated):
            return (f"prompt names subject {subject.get('id', '?')} but never restates its "
                    f"persistent identity tokens {restated}; state-once subjects drift "
                    f"between shots (restate per shot)")
    return None


def compile_shot_from_plan(plan, shot, cfg=None, contract=None):
    """Compile one plan shot through film_ir; returns the single-shot manifest."""
    cfg = cfg or _cfg_for(plan, shot)
    ir = _ir_shot(plan, shot,
                  contract if contract is not None else plan.get('film_ir_prompt_contract'))
    manifest = film_ir.compile_film({'film_id': plan['id'], 'shots': [ir]}, _tier_profile(shot, cfg))
    restatement = identity_restatement_problem(plan, shot)
    if restatement:
        manifest['violations'].append({'shot': shot['id'], 'kind': 'identity_restatement_warning',
                                       'detail': restatement})
    if (shot.get('prompt_contract') or plan.get('film_ir_prompt_contract')) == 'h3_fl2va_v1':
        for problem in film_ir.writing_layer_problems(shot.get('prompt') or ''):
            manifest['violations'].append({'shot': shot['id'], 'kind': 'writing_layer_warning',
                                           'detail': problem})
    return manifest


def _blocking(manifest):
    return [v for v in manifest['violations'] if v['kind'] in BLOCKING_VIOLATIONS]


def compiled_request(plan, shot, task, cfg):
    """Single construction entry: (remote_root, request, manifest). Raises on violations."""
    from .first_frame import validate_policy, enabled, require_profile
    validate_policy(plan)  # policy parity with the legacy constructor
    if enabled(plan):
        require_profile(cfg)
        if len(shot.get('reference_images', [])) != 1:
            raise ValueError('first_frame_only requires exactly one reviewed reference before dispatch')
    manifest = compile_shot_from_plan(plan, shot, cfg)
    blocking = _blocking(manifest)
    if blocking:
        raise ValueError('film_ir compile violations block generation (no legacy fallback): '
                         + json_dumps(blocking))
    compiled = manifest['shots'][0]
    segment = compiled['segments'][0]
    request = dict(segment['request'])
    request['task_id'] = task['id'].replace(':', '-')  # worker transport identity (parity)
    remote = cfg['remote_root'].rstrip('/') + '/tasks/' + digest({
        'task': task['id'], 'shot': shot, 'visual_prompt': request['prompt'],
        'prompt_contract': 'film_ir_v1', 'segment': segment['id']})[:24]
    request['output_dir'] = remote + '/output'
    return remote, request, manifest


def json_dumps(value):
    import json
    return json.dumps(value, ensure_ascii=False)


def stage_reference(ref, expected_sha, staged_dir, index):
    """Copy a keyframe into the attempt dir and verify the COPY's content hash.

    The copy is what gets hashed AND what gets uploaded, so the bytes that were
    verified are exactly the bytes that ship (TOCTOU-free binding). Returns the
    staged path; raises when the content no longer matches compile time."""
    from pathlib import Path as _P
    import shutil
    staged_dir = _P(staged_dir)
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged = staged_dir / f'reference_{index}{_P(ref).suffix}'
    shutil.copyfile(ref, staged)
    actual = film_ir.kf_identity([str(staged)])[0]
    if actual != expected_sha:
        raise ValueError(f'keyframe content changed since compile ({_P(ref).name}); recompile — '
                         'refusing to dispatch under the old segment identity')
    return str(staged)


def shadow_report(plan, shot, task, cfg, legacy_remote, legacy_request):
    """Field-by-field legacy-vs-film_ir diff. Empty 'diffs' == safe to switch."""
    remote, request, manifest = compiled_request(plan, shot, task, cfg)
    diffs = []

    def compare(field):
        if legacy_request.get(field) != request.get(field):
            diffs.append({'field': field, 'legacy': legacy_request.get(field),
                          'film_ir': request.get(field)})

    for field in ('task_id', 'prompt', 'duration_seconds', 'aspect_ratio', 'seed', 'generation'):
        compare(field)
    # reference_images parity: count via the remote naming, IDENTITY via content —
    # the compiled kf_shas must equal the plan refs' shas in order (catches swap,
    # reorder and drop that a name-only comparison cannot see). The remote ROOT
    # differs by design (segment-bound digest), so only the reference_N names match.
    def _ref_names(root, refs):
        return [Path(r).name if r.startswith(root + '/') else f'reference_{i}{Path(r).suffix}'
                for i, r in enumerate(refs)]

    legacy_refs = legacy_request.get('reference_images') or list(shot.get('reference_images', []))
    if _ref_names(legacy_remote, legacy_refs) != _ref_names(remote, request['reference_images']):
        diffs.append({'field': 'reference_images',
                      'legacy': _ref_names(legacy_remote, legacy_refs),
                      'film_ir': _ref_names(remote, request['reference_images'])})
    compiled = manifest['shots'][0]
    plan_refs = [r for r in shot.get('reference_images', []) if isinstance(r, str)]
    if plan_refs and film_ir.kf_identity(plan_refs) != compiled['kf_shas']:
        diffs.append({'field': 'reference_identity',
                      'legacy': film_ir.kf_identity(plan_refs), 'film_ir': compiled['kf_shas']})
    return {'shot': shot['id'], 'diffs': diffs,
            'segment': compiled['segments'][0]['id'],
            'violations': manifest['violations'],
            'accounting': compiled['accounting'],
            'duration_tier_seconds': request['duration_seconds']}


def shadow_entry(plan, shot, task, cfg, legacy_remote, legacy_request):
    """Shadow mode must be side-effect-free on the legacy path: a compile failure
    (t2va shot without keyframes, infeasible slot, …) is RECORDED, never raised —
    the legacy request still submits and the shot still succeeds."""
    try:
        return shadow_report(plan, shot, task, cfg, legacy_remote, legacy_request)
    except Exception as exc:  # noqa: BLE001 - the report itself must never kill the shot
        return {'shot': shot['id'], 'shadow_failed': f'{type(exc).__name__}: {exc}'[:500]}


def _probe_video(path):
    from .media import probe
    info = probe(path)
    v = next((s for s in info['streams'] if s.get('codec_type') == 'video'), None)
    if v is None:
        raise ValueError(f'{path}: no video stream to render')
    nb = int(v['nb_frames']) if str(v.get('nb_frames', '')).isdigit() else None
    seconds = float(v.get('duration') or info['format']['duration'])
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f'{path}: video duration {seconds!r} is not usable')
    return nb, seconds, (int(v['width']), int(v['height'])), v.get('r_frame_rate')


def _byte_preservable(path, frame_rate, size):
    """Same contract as normalize_shot's fast path: only already-conforming assets
    skip the re-encode. avg_frame_rate too (VFR sources keep r_frame_rate 24/1 but
    drift timelines on concat — opus E4); canonical time_base (mixed timebases break
    concat demuxer timestamps); codec/pix_fmt/SAR/streams or concat breaks."""
    from .media import probe
    info = probe(path)
    v = next(s for s in info['streams'] if s.get('codec_type') == 'video')
    return (frame_rate == f'{film_ir.FPS}/1' and v.get('avg_frame_rate') == f'{film_ir.FPS}/1'
            and v.get('time_base') == '1/12288' and size == (1080, 1920)
            and v.get('codec_name') == 'h264' and v.get('pix_fmt') == 'yuv420p'
            and v.get('sample_aspect_ratio') == '1:1' and not v.get('side_data_list')
            and len(info['streams']) == 1)


def render_compiled_shot(plan, shot, src, dest, submitted=None):
    """Settle the measured artifact against the compiled slot and render frame-exact.

    Replaces normalize_shot/whole_action for the film_ir pipeline: integer slot
    frames, -frames:v cap plus a produced-frames assertion, no trailing setpts.
    `submitted` is the manifest persisted at dispatch time: the rendered artifact
    must settle against THE segment that was sent, not a fresh compile over
    same-path-swapped keyframes.
    """
    manifest = compile_shot_from_plan(plan, shot)
    blocking = _blocking(manifest)
    if blocking:
        raise ValueError('film_ir compile violations block rendering: ' + json_dumps(blocking))
    if submitted is not None and submitted.get('shots'):
        was = submitted['shots'][0]
        now = manifest['shots'][0]
        if (was['segments'][0]['id'] != now['segments'][0]['id']
                or was.get('kf_shas') != now.get('kf_shas')):
            raise ValueError(f'{shot["id"]}: segment identity changed since dispatch '
                             '(same-path keyframe swap?) — refusing to settle the artifact '
                             'under a different segment')
    compiled = manifest['shots'][0]
    nb, seconds, (width, height), frame_rate = _probe_video(src)
    if frame_rate != f'{film_ir.FPS}/1':
        raise ValueError(f'{shot["id"]}: native frame rate {frame_rate} != {film_ir.FPS}/1')
    measured = film_ir.artifact_frames(nb, seconds)
    edit, warnings = film_ir.settle_accounting(compiled, measured, seconds)
    slot = compiled['accounting']['slot_frames']
    from .framing import filter_for
    from .media import ff
    # settb=AVTB before setpts: coarse source timebases truncate the scaled timestamps
    # and fps=24 cannot recover the lost precision (astra: coarse-tb sources undercount).
    vf = (f'settb=AVTB,setpts={edit["playback_rate"]:.6f}*PTS,'
          + filter_for(1080, 1920, plan.get('framing_policy')))
    ff('-i', src, '-an', '-vf', vf, '-frames:v', str(slot),
       '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', dest)
    out_nb, out_seconds, out_size, _ = _probe_video(dest)
    produced = film_ir.artifact_frames(out_nb, out_seconds)
    if produced != slot:
        raise ValueError(f'{shot["id"]}: produced {produced} frames != slot {slot}')
    if out_size != (1080, 1920):
        raise ValueError(f'{shot["id"]}: rendered {out_size[0]}x{out_size[1]} != 1080x1920')
    return {'segment': compiled['segments'][0]['id'],
            'native_frames': measured, 'native_seconds': seconds,
            'slot_frames': slot, 'produced_frames': produced,
            'playback_rate': edit['playback_rate'],
            'conditioning': compiled['conditioning'],
            'accounting': compiled['accounting'],
            'settle_warnings': warnings}


def validate_source_shot(plan, shot):
    """Submit-time pre-check for reused assets (fail at enqueue, not after NPU burn):
    fit contract, zero window start, and duration feasibility. Feasibility compares
    SOURCE SECONDS against the slot seconds — source frame counts live on the
    source timebase (a 12fps/48f asset legitimately fills a 96f/24fps slot)."""
    fit = shot.get('source_fit_policy', 'source_window')
    if fit not in ('source_window', 'whole_action'):
        raise ValueError(f'{shot["id"]}: film_ir supports source_window|whole_action for reused '
                         f'assets, got {fit!r}')
    if float(shot.get('source_start', 0) or 0) != 0:
        raise ValueError(f'{shot["id"]}: film_ir source rendering requires source_start == 0')
    nb, seconds, _, _ = _probe_video(shot['source_path'])
    slot = _slot_frames_for(plan, shot)
    slot_seconds = slot / film_ir.FPS
    if fit == 'source_window' and seconds + 0.5 / film_ir.FPS < slot_seconds:
        raise ValueError(f'{shot["id"]}: reused asset is {seconds:.3f}s < slot {slot_seconds:.3f}s; '
                         'refuse frozen padding or silent timing change')
    if fit == 'whole_action':
        playback = slot_seconds / seconds
        if not 1 / 1.5 - 1e-9 <= playback <= 1.5 + 1e-9:
            raise ValueError(f'{shot["id"]}: whole-action reuse playback {playback:.3f}x exceeds '
                             'natural speed bounds; retime storyboard')
    return {'shot': shot['id'], 'fit': fit, 'source_seconds': seconds, 'slot_frames': slot}


def render_source_shot(plan, shot, src, dest):
    """Frame-exact slot for a REUSED asset (source_path): the same integer-slot
    contract as generated shots, so a mixed timeline closes exactly. Replaces
    normalize_shot's float -t window, which rounded each source shot up by a
    frame (astra: 2340 r1 S02 143f vs slot 142f, whole film 1102 vs 1103).

    source_window: cut the leading window (legacy reuse semantics).
    whole_action: the repair compilers' intent — the COMPLETE clip is speed-ramped
    into the (possibly reflowed) slot, mirroring the generated-shot settle path."""
    validate_source_shot(plan, shot)
    if Path(src) != Path(shot['source_path']):
        # The validated asset and the rendered asset must be the same bytes; a
        # divergence would let the pre-check pass on one file and render another.
        raise ValueError(f'{shot["id"]}: render source {src} != plan source_path '
                         f'{shot["source_path"]}')
    fit = shot.get('source_fit_policy', 'source_window')
    slot = _slot_frames_for(plan, shot)
    slot_seconds = slot / film_ir.FPS
    nb, seconds, (width, height), frame_rate = _probe_video(src)
    measured = film_ir.artifact_frames(nb, seconds)
    # Byte-preserve only under the FULL contract: avg frame rate (not just r_frame_rate,
    # which stays 24/1 on VFR sources), container duration within half a frame of the
    # slot, zero start_time (edit lists), nb_frames == slot, and the pipeline's
    # canonical time_base — mixed time_bases break concat demuxer timestamps (astra E:
    # two CFR assets at tb 1/12288 vs 1/24000 concatenated to 94 frames instead of 48).
    from .media import ff
    if (measured == slot and _byte_preservable(src, frame_rate, (width, height))
            and _container_exact(src, slot)):
        import shutil
        shutil.copyfile(src, dest)
        return {'segment': None, 'source': 'reused_asset_byte_preserved', 'fit': fit,
                'slot_frames': slot, 'produced_frames': measured,
                'source_seconds': seconds, 'settle_warnings': []}
    from .framing import filter_for
    if fit == 'whole_action':
        # playback is a DURATION ratio: source PTS live on the source timebase, and
        # fps=24 then resamples the scaled timeline to exactly slot_frames regardless
        # of the source frame rate.
        playback = slot_seconds / seconds
        if not 1 / 1.5 - 1e-9 <= playback <= 1.5 + 1e-9:
            raise ValueError(f'{shot["id"]}: whole-action reuse playback {playback:.3f}x exceeds '
                             'natural speed bounds; retime storyboard')
        # settb=AVTB first: setpts truncates in the SOURCE timebase, and a coarse one
        # (12fps source at tb 1/12) loses sub-tick precision that fps=24 cannot recover
        # — a 97f slot delivered 96. AVTB makes the scaling exact (astra, reproduced).
        vf = (f'settb=AVTB,setpts={playback:.6f}*PTS,'
              + filter_for(1080, 1920, plan.get('framing_policy')))
        ff('-i', src, '-an', '-vf', vf, '-frames:v', str(slot),
           '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', dest)
        label = 'reused_asset_speed_ramped'
    else:
        if seconds + 0.5 / film_ir.FPS < slot_seconds:
            raise ValueError(f'{shot["id"]}: reused asset is {seconds:.3f}s < slot '
                             f'{slot_seconds:.3f}s; refuse frozen padding or silent timing change')
        ff('-i', src, '-an', '-vf', filter_for(1080, 1920, plan.get('framing_policy')),
           '-frames:v', str(slot), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
           '-pix_fmt', 'yuv420p', dest)
        label = 'reused_asset_retimed'
    out_nb, out_seconds, out_size, _ = _probe_video(dest)
    produced = film_ir.artifact_frames(out_nb, out_seconds)
    if produced != slot:
        raise ValueError(f'{shot["id"]}: produced {produced} frames != slot {slot}')
    if out_size != (1080, 1920):
        raise ValueError(f'{shot["id"]}: rendered {out_size[0]}x{out_size[1]} != 1080x1920')
    return {'segment': None, 'source': label, 'fit': fit,
            'slot_frames': slot, 'produced_frames': produced,
            'source_seconds': seconds, 'settle_warnings': []}


def _container_exact(path, slot):
    """Byte-preserve needs the CONTAINER duration within half a frame of the slot
    and a zero start (edit lists shift decode vs nb_frames). The container duration
    is read here — a stream-duration check misses stream/container mismatches."""
    from .media import probe
    info = probe(path)
    fmt = info['format']
    try:
        container_seconds = float(fmt['duration'])
    except (KeyError, TypeError, ValueError):
        return False
    start = fmt.get('start_time')
    return abs(container_seconds - slot / film_ir.FPS) <= 0.5 / film_ir.FPS + 1e-9 and \
        (start is None or float(start) == 0.0)


def _slot_frames_for(plan, shot):
    start_f = film_ir.frames(_slot_start(plan, shot['id']))
    return film_ir.frames(_slot_start(plan, shot['id']) + float(shot['duration_seconds'])) - start_f
