"""Context IR v3 compiler: film_ir -> build_manifest -> Ascend requests + edit_plan.

POC scope (docs/context_ir_design_v3.md): single-segment shots, compiled motion
prompts, feasibility violations, frame accounting, artifact-hash lock seeds.
Not yet: UUID patch layer, candidates/best-of-N, KF QA, seam assembly.
"""
import hashlib
import json
import math

FPS = 24  # single timebase: H3 output, edit, delivery (N7)

# --- Timebase law: the ONLY place duration/tier/mark conversions live. ---
# Measured native frame counts per requested duration (668 local artifacts, zero
# variance). The backend does NOT emit seconds*24: it quantizes to the next
# N >= 24*d with N == 5 (mod 17). Official Context-IR ruling (2026-10-06): the
# FL2VA alignment mark equals the NATIVE seconds of the requested tier
# (7s request -> 7.29 mark, 6s -> 6.58, 8s -> 8.00), never the integer request.
MEASURED_NATIVE_FRAMES = {4: 107, 5: 124, 6: 158, 7: 175, 8: 192, 9: 226,
                          10: 243, 11: 277, 12: 294, 14: 345}


def native_frames_law(tier_seconds):
    """Smallest N >= 24*tier with N == 5 (mod 17); matches all measured tiers."""
    base = tier_seconds * FPS
    return base + (5 - base) % 17


for _d, _n in MEASURED_NATIVE_FRAMES.items():
    assert native_frames_law(_d) == _n, f'tier law disagrees with measurement at {_d}s'


def native_frames(tier_seconds):
    if tier_seconds in MEASURED_NATIVE_FRAMES:
        return MEASURED_NATIVE_FRAMES[tier_seconds]
    return native_frames_law(tier_seconds)  # measured table wins; law extends


def native_seconds(profile):
    """Mark value for the FL2VA alignment line: native frame-grid seconds."""
    return profile['frames'] / FPS

# camera × rate -> English phrase; the compiled prompt is the single truth (v3 §2).
# Every (type, rate) combination must exist explicitly: silent fallbacks rewrite
# authored intent (pull+normal must not quietly become 'slow').
MOTION_PHRASES = {
    ('pan', 'slow'): 'a slow lateral camera move',
    ('pan', 'normal'): 'a steady lateral camera move',
    ('pan', 'fast'): 'a quick lateral camera move',
    ('push', 'slow'): 'a slow push-in',
    ('push', 'normal'): 'a steady push-in',
    ('push', 'fast'): 'a quick push-in',
    ('pull', 'slow'): 'a slow pull-back',
    ('pull', 'normal'): 'a steady pull-back',
    ('pull', 'fast'): 'a quick pull-back',
    ('orbit', 'slow'): 'a slow orbital camera move',
    ('orbit', 'normal'): 'a steady orbital camera move',
    ('hold', 'slow'): 'a fixed camera',
    ('hold', 'normal'): 'a fixed camera',
    ('hold', 'fast'): 'a fixed camera',
}
RATES = ('slow', 'normal', 'fast')
AMPLITUDE_HINT = {'S': 'The camera movement stays subtle.', 'M': '', 'L': 'The camera movement is pronounced.'}


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def frames(seconds):
    return int(math.floor(seconds * FPS + 1e-6))


def artifact_frames(nb_frames, duration_seconds):
    """Exact frame count of a MEASURED artifact: trust nb_frames; derive from video
    duration with round() only as fallback (floor miscounts non-integer boundaries,
    e.g. 4.458333s is 107 frames, not 106)."""
    if isinstance(nb_frames, int) and not isinstance(nb_frames, bool) and nb_frames > 0:
        return nb_frames
    return max(1, int(round(duration_seconds * FPS)))


def _file_sha(path):
    import hashlib
    # No path-keyed cache: a same-path content swap must re-key (that is the attack
    # this binding exists to catch). Compile is cold-path; reading KFs is cheap.
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def kf_identity(refs):
    """Keyframe identity binds IMAGE CONTENT, not paths (same-path swaps must re-key)."""
    import os
    for r in refs:
        if not isinstance(r, str) or not os.path.isfile(r):
            raise ValueError(f'keyframe reference is not an existing file: {r!r}')
    return [_file_sha(r) for r in refs]


def render_prompt(shot, conditioning=None):
    """Compile motion + subject into the final prompt; frame binding follows RESOLVED conditioning."""
    motion = shot.get('motion') or {}
    camera = (motion.get('camera') or {})
    cam_type = camera.get('type', 'hold')
    rate = camera.get('rate', 'slow')
    if cam_type not in ('pan', 'push', 'pull', 'orbit', 'hold'):
        raise ValueError(f'unknown camera type: {cam_type!r}')
    if rate not in RATES:
        raise ValueError(f'unknown camera rate: {rate!r}')
    phrase = MOTION_PHRASES[(cam_type, rate)]
    # Direction changes the phrase (pan left != pan right), so creative edits re-key the segment.
    direction = camera.get('direction')
    if cam_type != 'hold' and direction in ('left', 'right', 'clockwise', 'counterclockwise'):
        phrase = phrase + (' to the left' if direction == 'left' else
                           ' to the right' if direction == 'right' else
                           ' clockwise' if direction == 'clockwise' else ' counterclockwise')
    subject = (motion.get('subject') or {}).get('action') or 'the subject holds still'
    parts = [f"{shot['id']}: {phrase} over the scene. {subject[0].upper() + subject[1:]}"
             if not subject.endswith('.') else f"{shot['id']}: {phrase} over the scene. {subject}"]
    amplitude = AMPLITUDE_HINT.get(camera.get('amplitude', 'M'), '')
    if amplitude:
        parts.append(amplitude)
    if conditioning is None:
        conditioning = shot.get('conditioning') or 'first_only'
    parts.append(binding_suffix(conditioning))
    return ' '.join(parts)


FL2VA_ALIGN_FIRST_LAST = ('How the reference pictures align with the target video — Picture 1 (from Shot 1) '
                          'aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) '
                          'aligns with the {end}-second mark of the target video.')
FL2VA_ALIGN_FIRST_ONLY = ('For the target video, at 0.00 seconds into the target video, <Picture 1> '
                          '(from [Shot 1]) is fully referenced.')
FL2VA_FIELDS = ('integrated_multimodal_description:', 'overall_soundscape:', 'non_diegetic_music:')

# Writing-layer lint (docs/h3_writing_layer.md, R1/R3 + length baseline).
# Machine-checkable quality only — never blocking; surfaces as compile warnings.
_CAMERA_ACTION = __import__('re').compile(
    r'\bcamera\b[^.]*\b(executes|performs|holds|begins|continues|glides|pushes|pulls|pans|tilts|'
    r'trucks|keeps|maintains|remains)\b|\b(push-in|pull out|arc shot)\b', __import__('re').I)


def writing_layer_problems(prompt):
    """Deterministic R1/R3/length lint on an official-structure FL2VA prompt.

    Golden corpus must pass clean (tests enforce); our pre-writing-layer
    prompts warn — that gap is exactly what the next film's hand-authoring
    closes. Returns a list of human-readable problems."""
    problems = []
    try:
        desc = prompt.split('integrated_multimodal_description:', 1)[1].split('overall_soundscape:', 1)[0]
        snd = prompt.split('overall_soundscape:', 1)[1].split('non_diegetic_music:', 1)[0]
    except IndexError:
        return []  # structural contract violations are fl2va_problem's job
    if not _CAMERA_ACTION.search(desc):
        problems.append('R1: no camera-action sentence (verb + duration), a noun phrase '
                        'alone gets ignored by the model (r8 push-in drift)')
    if len([s for s in __import__('re').split(r'(?<=[.!?])\s+', snd) if s.strip()]) < 2:
        problems.append('R3: soundscape is a single ambient sentence, not a narrated '
                        'event progression (golden baseline is 3-6 sentences)')
    if len(desc.split()) < 150:
        problems.append(f'baseline: description is {len(desc.split())} words, golden '
                        f'corpus runs 196-224 — path lacks intermediate layers')
    return problems


def fl2va_problem(prompt, conditioning, native_seconds, has_end_kf):
    """Structural check of the official H3 FL2VA prompt (skills/h3-prompt-writing).

    The official alignment line IS the frame-binding mechanism, so under this contract
    nothing is appended; instead the line must match the compiled slot exactly and all
    three official fields must appear in order. The S.SS end mark equals the NATIVE
    frame-grid seconds of the requested tier (official Context-IR ruling 2026-10-06:
    request 7 -> mark 7.29 = 175 frames, request 6 -> 6.58, request 8 -> 8.00).
    Returns None when valid, else a human-readable problem."""
    lines = prompt.splitlines()
    if not lines or not lines[0].strip():
        return 'empty prompt'
    end = f'{native_seconds:.2f}'
    if conditioning == 'first_last':
        if lines[0].strip() != FL2VA_ALIGN_FIRST_LAST.format(end=end):
            return (f'alignment line must be the official FL2VA first/last form whose '
                    f'Picture 2 mark is the {end}-second slot end')
        if not has_end_kf:
            return 'first_last FL2VA requires the end keyframe'
    elif lines[0].strip() != FL2VA_ALIGN_FIRST_ONLY:
        return 'alignment line must be the official I2VA first-frame form'
    if len(lines) < 2 or lines[1].strip():
        return 'a blank line must separate the alignment line from the three fields'
    # Field headers must stand alone at line starts, appear once each, in order,
    # and carry nonempty content (astra r1 P1: substring search passed empty bodies).
    positions = []
    for field in FL2VA_FIELDS:
        # Official format puts content inline: "field: ..." at a line start (base-en.txt).
        hits = [i for i, ln in enumerate(lines) if ln.startswith(field)]
        if not hits:
            return f'missing official field {field} at a line start'
        if len(hits) > 1:
            return f'duplicate official field {field}'
        positions.append(hits[0])
    if positions != sorted(positions):
        return 'official fields must appear in order'
    for idx, (field, pos) in enumerate(zip(FL2VA_FIELDS, positions)):
        end = positions[idx + 1] if idx + 1 < len(positions) else len(lines)
        content = '\n'.join([lines[pos][len(field):]] + lines[pos + 1:end]).strip()
        if not content:
            return f'official field {field} has no content'
    return None


def binding_suffix(conditioning):
    """v3 frame-binding clauses appended to prompts under the 'v3_binding' contract."""
    clause = 'Begin exactly at the first reference frame'
    if conditioning == 'first_last':
        clause += ' and end exactly at the last reference frame'
    return clause + '. No cuts, no on-screen text, lettering or overlays.'


def compile_shot(shot, profile, violations):
    """One shot -> dict(segments, edit_entries, accounting) with feasibility checks."""
    # Integer frame boundaries are the single source of slot length (no float-derived off-by-one).
    start_f = frames(shot['slot_start'])
    slot = frames(shot['slot_start'] + shot['slot_seconds']) - start_f
    native = int(profile['frames'])  # legal frame count from model_profile (N7)
    first_kf, last_kf = _shot_keyframes(shot)
    conditioning = _shot_conditioning(shot, first_kf, last_kf, violations)
    if conditioning is None:
        return None
    first_kf, last_kf = _condition_keyframes(shot, conditioning, first_kf, last_kf, violations)
    missing_start = conditioning in ('first_only', 'first_last') and not first_kf
    if missing_start:
        violations.append({'shot': shot['id'], 'kind': 'missing_start_kf',
                           'detail': 'no start keyframe; t2va would be required (unsupported in POC)'})
    refs = first_kf + last_kf if conditioning == 'first_last' else first_kf
    # N10: quality first — no silent overspeed. Tiered: ok <=1.1, warn <=1.25, error beyond
    # (0.5..1.5 remains legal only for explicitly authored speed ramps).
    playback = slot / native if native else float('inf')
    infeasible = not (1 / 1.5 - 1e-9 <= playback <= 1.5 + 1e-9)
    if infeasible:
        violations.append({'shot': shot['id'], 'kind': 'slot_infeasible',
                           'detail': f'slot {slot}f vs native {native}f -> playback {playback:.3f}x '
                                     f'exceeds [1/1.5, 1.5]; options: regen other duration tier / split shot / edit script'})
    elif not 1 / 1.25 - 1e-9 <= playback <= 1.25 + 1e-9:
        violations.append({'shot': shot['id'], 'kind': 'slot_retune_warning',
                           'detail': f'playback {playback:.3f}x beyond ±25%; prefer a closer duration tier'})
    elif not 1 / 1.1 - 1e-9 <= playback <= 1.1 + 1e-9:
        violations.append({'shot': shot['id'], 'kind': 'slot_tight_warning',
                           'detail': f'playback {playback:.3f}x beyond ±10%'})
    prompt = _shot_prompt(shot, conditioning, profile, last_kf, violations)
    generation = {'steps': profile.get('steps', 50), 'short_edge': profile.get('short_edge', 768)}
    refs, kf_shas, executable = _keyframe_files(shot, refs, missing_start, infeasible, violations)
    # Segment identity covers every effective sampling input: seed, generation params and
    # keyframe IMAGE CONTENT (same-path swaps re-key; path-only hashing would hit stale caches).
    seg_id = _sha({'shot': shot['id'], 'role': 'primary', 'kfs': kf_shas,
                   'prompt': prompt, 'seed': shot.get('seed', 1), 'generation': generation,
                   'native_frames': native, 'conditioning': conditioning})[:16]
    request = {'schema_version': 1, 'task_id': f"{shot['id']}-{seg_id}",
               'prompt': prompt,
               # Request seconds are the authored tier parameter, NOT derived from the
               # expected native frames (measured 158f at the 6s tier must still
               # request 6s; deriving 158/24 -> 7s would silently change the tier).
               'duration_seconds': max(4, int(profile.get('seconds') or round(native / FPS))),
               'aspect_ratio': '9:16', 'seed': shot.get('seed', 1),
               'generation': {'steps': generation['steps'],
                              'short_edge': profile.get('short_edge', 768)}}
    if refs:
        request['reference_images'] = refs
    # N11' drop rule binds the AUDIO processing rate (atempo = 1/playback), not playback:
    # a 0.75x video slowdown speeds audio 1.333x, which already degrades the bed.
    finite = math.isfinite(playback) and playback > 0
    audio_rate = 1 / playback if finite else None
    edit = {'id': _sha({'shot': shot['id'], 'segment': seg_id, 'role': 'edit_primary'})[:16],
            'shot': shot['id'], 'segment': seg_id,
            'timeline_range': [start_f, start_f + slot],
            'source_range': [0, min(slot, native)],  # settle_accounting rewrites from measured frames
            'playback_rate': round(playback, 6) if finite else None,
            'foley': {'keep': bool(finite and abs(audio_rate - 1) <= 0.25),
                      'audio_rate': round(audio_rate, 6) if finite else None},
            'conditioning': conditioning}
    accounting = {'shot': shot['id'], 'native_frames': native, 'slot_frames': slot,
                  'dropped': max(0, native - slot), 'inserted_net': max(0, slot - native),
                  'final_frames': slot,
                  'formula': 'N_final = sum(N_i mapped) - D + A_net'}
    # P0 violations must not reach a generation stage: mark the segment non-executable.
    return {'id': shot['id'], 'conditioning': conditioning, 'executable': executable,
            'kf_shas': kf_shas,
            'segments': [{'id': seg_id, 'request': request, 'edit': edit}],
            'accounting': accounting}


def _shot_keyframes(shot):
    # Non-string entries (authoring slips like [None]) count as missing, not as keyframes.
    first_kf = [r for r in ((shot.get('keyframes') or {}).get('start') or []) if isinstance(r, str)]
    last_kf = [r for r in ((shot.get('keyframes') or {}).get('end') or []) if isinstance(r, str)]
    return (first_kf, last_kf)


def _default_conditioning(shot):
    conditioning = shot.get('conditioning')
    if conditioning is None:
        needs_end = bool(shot.get('must_show') or shot.get('entities_must_show')
                         or shot.get('state_delta') or shot.get('facts_change')
                         or shot.get('continuity_bound'))
        conditioning = 'first_last' if needs_end else 'first_only'
    return conditioning


def _shot_conditioning(shot, first_kf, last_kf, violations):
    # v3 C3: default first_only; first_last when authored or the shot carries terminal
    # constraints (must_show / entities_must_show / state_delta / facts_change / continuity).
    conditioning = _default_conditioning(shot)
    if conditioning == 'first_last' and not (first_kf and last_kf):
        if shot.get('continuity_bound'):
            # Fail closed: fallback would break seam continuity between adjacent segments.
            violations.append({'shot': shot['id'], 'kind': 'missing_end_kf_blocked',
                               'detail': 'continuity-bound shot needs its end keyframe; blocking instead of '
                                         'first_only fallback'})
            return None
        violations.append({'shot': shot['id'], 'kind': 'missing_end_kf',
                           'detail': 'first_last conditioning needs both keyframes'})
        conditioning = 'first_only'
    if conditioning == 'first_only' and shot.get('continuity_bound'):
        # Explicit first_only must not silently bypass the seam protection either.
        violations.append({'shot': shot['id'], 'kind': 'continuity_first_only_conflict',
                           'detail': 'continuity-bound shots need first_last with an end keyframe'})
    return conditioning


def _condition_keyframes(shot, conditioning, first_kf, last_kf, violations):
    # Reference count contract: the backend interprets 2 refs as (first, last); extra
    # start frames under first_only would silently change the conditioning semantics.
    if conditioning == 'first_only' and len(first_kf) > 1:
        violations.append({'shot': shot['id'], 'kind': 'kf_count_invalid',
                           'detail': f'first_only carries {len(first_kf)} start frames; backend would read '
                                     f'the second as an end frame'})
        first_kf = first_kf[:1]
    if conditioning == 'first_last' and (len(first_kf) > 1 or len(last_kf) > 1):
        violations.append({'shot': shot['id'], 'kind': 'kf_count_invalid',
                           'detail': f'first_last expects exactly one start and one end frame '
                                     f'(got {len(first_kf)}+{len(last_kf)})'})
        first_kf, last_kf = first_kf[:1], last_kf[:1]
    return (first_kf, last_kf)


def _shot_prompt(shot, conditioning, profile, last_kf, violations):
    # Prompt truth: an authored prompt passes through verbatim ('legacy_verbatim', the
    # production plan contract — byte parity with the legacy constructor is what the
    # shadow diff proves); 'v3_binding' appends the frame-binding clauses. Structured
    # motion compilation (POC path) applies only when no authored prompt exists.
    authored = shot.get('prompt_authored')
    if isinstance(authored, str) and authored.strip():
        prompt = authored.strip()
        if shot.get('prompt_contract') == 'v3_binding':
            prompt = prompt + ' ' + binding_suffix(conditioning)
        elif shot.get('prompt_contract') == 'h3_fl2va_v1':
            problem = fl2va_problem(prompt, conditioning, native_seconds(profile), bool(last_kf))
            if problem:
                violations.append({'shot': shot['id'], 'kind': 'h3_fl2va_invalid', 'detail': problem})
        if (motion := shot.get('motion')) and (motion.get('camera') or motion.get('subject')):
            violations.append({'shot': shot['id'], 'kind': 'authored_prompt_overrides_motion',
                               'detail': 'structured motion is ignored under an authored prompt'})
    else:
        prompt = render_prompt(shot, conditioning)  # single prompt truth, compiled AFTER resolution
    return prompt


def _keyframe_files(shot, refs, missing_start, infeasible, violations):
    # Missing KF files are feasibility violations registered per shot, not crashes that
    # take down the whole compile (one bad shot must not erase the rest of the manifest).
    import os as _os
    missing_files = [r for r in refs if not _os.path.isfile(r)]
    if missing_files:
        violations.append({'shot': shot['id'], 'kind': 'missing_kf_file',
                           'detail': f'keyframe file(s) not found: {missing_files}'})
        refs, kf_shas = [], []
        executable = False
    else:
        kf_shas = kf_identity(refs)
        executable = not (missing_start or infeasible)
    return (refs, kf_shas, executable)


def compile_film(film_ir, profile, backend_note=''):
    violations = []
    compiled = []
    for s in film_ir['shots']:
        result = compile_shot(s, profile, violations)
        if result is not None:  # continuity-blocked shots are excluded, not silently degraded
            compiled.append(result)
    manifest = {
        'schema_version': 1, 'film_id': film_ir['film_id'], 'fps': FPS,
        'profile': profile, 'backend_note': backend_note,
        'style_bible_ref': film_ir.get('style_bible_ref'),
        'entity_refs': film_ir.get('entity_refs', {}),
        'shots': compiled,
        'violations': violations,
    }
    return manifest


def settle_accounting(shot_compiled, measured_frames, measured_seconds=None):
    """Recompute playback/accounting from the MEASURED artifact frame count (P0: request
    seconds lie; and floor(duration) miscounts — callers pass artifact_frames()).

    Hard-bound check runs BEFORE any mutation, so a rejected settle leaves the ledger clean.
    Returns (edit, warnings): tiers mirror compile-time feasibility (1.1 warn / 1.25 retune).
    """
    slot = shot_compiled['accounting']['slot_frames']
    measured = int(measured_frames)
    if measured <= 0:
        raise ValueError(f'measured frames {measured_frames} is not a positive count')
    playback = slot / measured
    if not 1 / 1.5 - 1e-9 <= playback <= 1.5 + 1e-9:
        raise ValueError(f"{shot_compiled['id']}: measured playback {playback:.3f}x exceeds hard bound; "
                         f'regenerate at another duration tier')
    warnings = []
    if not 1 / 1.25 - 1e-9 <= playback <= 1.25 + 1e-9:
        warnings.append({'shot': shot_compiled['id'], 'kind': 'slot_retune_warning',
                         'detail': f'measured playback {playback:.3f}x beyond ±25%; prefer another tier'})
    elif not 1 / 1.1 - 1e-9 <= playback <= 1.1 + 1e-9:
        warnings.append({'shot': shot_compiled['id'], 'kind': 'slot_tight_warning',
                         'detail': f'measured playback {playback:.3f}x beyond ±10%'})
    edit = shot_compiled['segments'][0]['edit']
    edit['playback_rate'] = round(playback, 6)
    audio_rate = 1 / playback
    edit['foley']['audio_rate'] = round(audio_rate, 6)
    edit['foley']['keep'] = abs(audio_rate - 1) <= 0.25
    edit['source_range'] = [0, measured]  # whole measured source maps into the slot
    acc = shot_compiled['accounting']
    acc.update(native_frames=measured, dropped=max(0, measured - slot),
               inserted_net=max(0, slot - measured), final_frames=slot)
    if measured_seconds is not None:
        acc['measured_seconds'] = measured_seconds
    return edit, warnings


def dry_run(manifest):
    """make-style report: what would be generated, what is blocked."""
    lines = []
    total_seconds = 0
    for shot in manifest['shots']:
        for seg in shot['segments']:
            req = seg['request']
            est = req['duration_seconds'] * 50 / 4.5  # ~50s wall per 4.5s generated (measured)
            total_seconds += est
            lines.append(f"GEN {req['task_id']}: {req['duration_seconds']}s native, "
                         f"{len(req.get('reference_images', []))} refs, "
                         f"edit {seg['edit']['playback_rate']}x -> slot {shot['accounting']['slot_frames']}f")
    for v in manifest['violations']:
        lines.append(f"VIOLATION {v['shot']}: {v['kind']} — {v['detail']}")
    lines.append(f"ESTIMATED GENERATION WALL: ~{total_seconds / 60:.1f} min (quality-first; NPU cost not optimized)")
    return '\n'.join(lines)


def recipe_hash(request, reference_shas, backend_fingerprint):
    """Cache/dedup key over effective inputs incl. the ACTUALLY-LOADED weight fingerprint.

    P0 from POC review: declared config != loaded config (turbo LoRA incident), so the
    fingerprint must come from a pinned/verified statement of what the backend serves.
    """
    return _sha({'request': request, 'reference_images': reference_shas,
                 'backend_fingerprint': backend_fingerprint})


def lock_entry(kind, artifact_path, artifact_sha256, payload):
    """selections/annotations lock seed: keyed by artifact content hash (N3)."""
    entry = {'kind': kind, 'artifact': artifact_path, 'artifact_sha256': artifact_sha256,
             'payload': payload}
    if 'recipe' in payload:
        entry['recipe_hash'] = payload['recipe']
    return entry
