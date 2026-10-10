"""Import verified image results into new candidate manifests; never approve or enqueue."""

import fcntl
import json
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from workflow.code_identity import code_identity

from PIL import Image
from . import backends, media, storyboard
from .core.io import digest, sha256, write_json
from .narrative_gate import require_review, review_manifest
from .plan import validate
from .storyboard import missing_previsuals
from .storyboard_batch import register, stage

def bind(job):
    source = Path(job['manifest_path']).resolve()
    if sha256(source) != job['manifest_sha256']:
        raise ValueError('manifest changed since asset request')
    draft = Path(job['draft_path']).resolve()
    if sha256(draft) != job['draft_sha256']:
        raise ValueError('draft changed since asset request')
    plan = json.loads(source.read_text(encoding="utf-8"))
    shots = {s['id']: s for s in plan['shots']}
    verified = []
    # Validate the complete product before writing any bindings.
    for asset in job['assets']:
        if asset['shot_id'] not in shots or asset['role'] not in ('start', 'end'):
            raise ValueError('invalid shot or asset role')
        from workflow.first_frame import reference_roles
        if asset['role'] not in reference_roles(plan, shots[asset['shot_id']]):
            raise ValueError('asset role contradicts explicit generation strategy')
        path = Path(asset['path']).resolve()
        if sha256(path) != asset['sha256']:
            raise ValueError('image checksum mismatch')
        with Image.open(path) as im:
            im.verify()
        verified.append((asset, path))
    keys = [(a['shot_id'], a['role']) for a, _ in verified]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate shot/role binding')
    receipts = []
    for asset, path in verified:
        dest = draft.parent / 'storyboard_images' / (asset['shot_id'] + '_' + asset['role'] + '_' + asset['sha256'][:16] + path.suffix)
        if dest.exists() and sha256(dest) != asset['sha256']:
            raise ValueError('destination checksum conflict')
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        shot = shots[asset['shot_id']]
        refs = list(shot.get('reference_images', []))
        if asset['role'] == 'start':
            shot['previsual_path'] = str(dest)
            refs = [str(dest)] + refs[1:]
        else:
            if not refs:
                raise ValueError('end reference has no start')
            refs = [refs[0], str(dest)]
        shot['reference_images'] = refs
        receipts.append(dict(asset, imported_path=str(dest), draft_sha256=job['draft_sha256'], status='candidate_not_approved_for_video'))
    validate(plan)
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()
    import hashlib
    out = draft.parent / ('manifest_assets_' + hashlib.sha256(payload).hexdigest()[:16])
    out.mkdir(exist_ok=True)
    write_json(out / 'manifest.json', plan)
    status = dict(state='candidate_structurally_valid', manifest_path=str(out / 'manifest.json'), manifest_sha256=sha256(out / 'manifest.json'), parent_manifest_sha256=job['manifest_sha256'], assets=receipts, missing_assets=missing_previsuals(plan), exact_manifest_review='pending', whole_board_review='pending', production_submission=False)
    write_json(out / 'status.json', status)
    return status


def bind_batch(packet):
    rows = []
    for job in packet['products']:
        try:
            rows.append(dict(product_id=job['product_id'], **bind(job)))
        except Exception as exc:
            rows.append(dict(product_id=job['product_id'], state='asset_binding_failed', error=str(exc), production_submission=False))
    return dict(items=rows, production_submission=False)



"""Bounded, resumable storyboard downstream; never starts a video controller.

Run as ``python -m workflow.storyboard_downstream --packet PACKET --output DIR
--through stage``.  Registration is opt-in via ``--through register`` and writes
only the existing singleton's deployment-request inbox.  Interrupted external
calls are reconciled from completed evidence, never blindly resubmitted.
"""


PHASES = ('collect', 'review', 'preflight', 'stage', 'register')
PROJECT = Path(__file__).resolve().parents[1]


class Paused(Exception):
    def __init__(self, state, detail):
        self.state, self.detail = state, detail
        super().__init__(str(detail))


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def checked(path, checksum):
    path = Path(path).expanduser().resolve()
    if not checksum or sha256(path) != checksum:
        raise ValueError('Changed or unbound file: ' + str(path))
    return path


def complete_assets(item):
    """Require the manifest's complete role set, not just the files found in jobs."""
    manifest = checked(item['manifest_path'], item['manifest_sha256'])
    checked(item['draft_path'], item['draft_sha256'])
    plan = read(manifest)
    expected = set()
    for shot in plan['shots']:
        if shot.get('source_path') or shot.get('production_mode') == 'deterministic_graphic':
            continue
        from workflow.first_frame import reference_roles
        roles = reference_roles(plan, shot)
        refs = shot.get('reference_images', [])
        for index, role in enumerate(roles):
            if len(refs) <= index or not Path(refs[index]).is_file():
                expected.add((shot['id'], role))
    tasks = {}
    for path in sorted(Path(item['image_jobs_dir']).glob('*.json')):
        task = read(path)
        if (task.get('product_id'), task.get('manifest_sha256')) != (item['product_id'], item['manifest_sha256']):
            continue
        if Path(task['manifest_path']).resolve() != manifest:
            raise ValueError('Image task points at another manifest')
        key = (task['shot_id'], task['role'])
        if key not in expected or key in tasks:
            raise ValueError('Unexpected or duplicate image role: ' + str(key))
        tasks[key] = task
    pending = [{'shot_id': s, 'role': r, 'state': tasks.get((s, r), {}).get('state', 'missing_job')}
               for s, r in sorted(expected) if tasks.get((s, r), {}).get('state') != 'completed']
    if pending:
        raise Paused('awaiting_images', {'expected': len(expected), 'completed': len(expected) - len(pending), 'pending': pending})
    assets = []
    for (shot, role), task in sorted(tasks.items(), key=lambda pair: (pair[0][0], pair[0][1] == 'end')):
        if task.get('approval') is False:
            raise ValueError('Image was rejected: ' + task['id'])
        if task.get('image_job_schema_version', 1) >= 2 and task.get('canvas_check', {}).get('status') != 'passed':
            raise ValueError('Image canvas not verified: ' + task['id'])
        image = checked(task['image_path'], task['image_sha256'])
        assets.append({'shot_id': shot, 'role': role, 'path': str(image), 'sha256': task['image_sha256']})
    return assets


def checkpoint(folder, name, identity, operation, evidence):
    """A failed/ambiguous attempt is a stop, not permission for another API call."""
    record_path = folder / (name + '.checkpoint.json')
    prior = read(record_path) if record_path.exists() else None
    if prior and prior['input_digest'] != identity:
        raise ValueError('Checkpoint inputs changed: ' + name)
    if prior:
        if prior['state'] == 'succeeded':
            return evidence()
        try:
            result = evidence()
        except Exception:
            raise Paused('isolated_' + name, {'checkpoint': str(record_path), 'reason': 'Previous attempt has no complete valid evidence; diagnose before any retry', 'prior_state': prior['state']})
        write_json(record_path, {**prior, 'state': 'succeeded', 'reconciled_at': time.time()})
        return result
    row = {'state': 'running', 'pid': os.getpid(), 'input_digest': identity, 'started_at': time.time()}
    write_json(record_path, row)
    try:
        operation()
        result = evidence()
        write_json(record_path, {**row, 'state': 'succeeded', 'finished_at': time.time()})
        return result
    except Exception as exc:
        write_json(record_path, {**row, 'state': 'failed', 'finished_at': time.time(), 'error_type': type(exc).__name__, 'error': str(exc)})
        raise


def verify_acceptance(path):
    result = read(path)
    if result.get('status') != 'ready_for_generation':
        raise ValueError('Preflight did not pass')
    for key in ('manifest', 'visual_review', 'edl', 'preview'):
        checked(result[key + '_path'], result[key + '_sha256'])
    require_review(result['manifest_path'], result['review_path'])
    plan = read(result['manifest_path'])
    edl = read(result['edl_path'])
    # Reuse the production gate itself, including visual review and exact audio.
    storyboard.reuse_preflight(
        {**plan, 'approved_story_preflight': {'path': str(path), 'sha256': sha256(path)}},
        {'effective_narration': plan['narration'], 'audio_path': edl['narration_path']},
        {'audio_path': edl['music_path']}, Path(path).parent)
    return result


def runtime_ready(packet):
    runtime = read(packet['renderer_runtime_path'])
    os.kill(int(runtime['pid']), 0)
    pairs = {'loaded_media_sha256': 'required_media_sha256', 'normalization_version': 'required_media_version',
             'story_review_version': 'required_story_review_version', 'story_preflight_version': 'required_story_preflight_version'}
    if not runtime.get('story_preflight_lane') or any(runtime.get(k) != packet[v] for k, v in pairs.items()):
        raise Paused('staged_runtime_pending', {'runtime_path': packet['renderer_runtime_path'], 'reason': 'Active runtime does not match required versions; only its owner may restart it'})
    if packet.get('required_first_frame_runtime'):
        from workflow.first_frame import require_runtime
        try:
            require_runtime(runtime,packet['required_first_frame_runtime'])
        except ValueError as exc:
            raise Paused('staged_runtime_pending', {'reason':str(exc)}) from exc


def preflight_pause(folder, error):
    path = folder / 'keyframe_review.json'
    if path.exists():
        review = read(path)
        if review.get('error_type') or review.get('diagnostic'):
            return Paused('isolated_review_service_failure', {'review_path': str(path),
                          'diagnostic': review.get('diagnostic'), 'error_type': review.get('error_type'),
                          'content_verdict': None, 'automatic_retry': False})
        if review.get('status') != 'automatic_review_passed':
            assessment = review.get('review', {})
            confidence = assessment.get('confidence')
            findings = assessment.get('findings')
            confidence_only = (assessment.get('verdict') == 'pass'
                               and type(confidence) in (int, float) and 0 <= confidence < .85
                               and isinstance(findings, list)
                               and all(isinstance(f, dict) and f.get('severity') == 'low' for f in findings))
            return Paused('isolated_review_confidence' if confidence_only else 'isolated_board_revision',
                          {'review_path': str(path), 'review_sha256': sha256(path),
                           'review': assessment, 'automatic_retry': False,
                           'blocker_code': 'INSUFFICIENT_REVIEW_CONFIDENCE' if confidence_only else 'BOARD_REVISION_REQUIRED',
                           'required_confidence': .85,
                           'next_action': 'Diagnose saved evidence and assessment scope; no automatic regeneration or repeat review' if confidence_only else 'Diagnose identified board defects before a bounded repair',
                           'deferred_checks': assessment.get('not_assessed', [])})
    return error


def validate_generation_requests(plan, backend):
    """Pure production request checks, with no SSH, task claim or admission."""
    from workflow.runner import visual_generation_request
    assigned = backends.assignments(backend, plan['shots'])
    requests = []
    for shot in plan['shots']:
        if shot.get('source_path'):
            continue
        built = visual_generation_request(plan, shot,
                         {'id': 'preflight-only-' + shot['id']}, assigned[shot['id']])
        # legacy returns (remote, request); film_ir returns (remote, request, manifest)
        request = built[1]
        requests.append({'shot_id': shot['id'], 'duration_seconds': shot['duration_seconds'],
                         'requested_duration_seconds': request['duration_seconds'],
                         'reference_count': len(shot.get('reference_images', [])),
                         'capability': assigned[shot['id']]['capability']})
    return requests


def validate_source_timing(plan):
    """Use the renderer's existing timing rule before spending on story reviews.

    film_ir plans check with the adapter's own rule set: the legacy whole_action
    formula and the submit pre-check disagree on boundaries and window length,
    and a plan that only fails at submit has already paid for one story review."""
    from workflow.film_ir_plan import pipeline_of, validate_source_shot
    if pipeline_of(plan) == 'film_ir':  # shadow keeps legacy behavior exactly
        for shot in plan['shots']:
            if shot.get('source_path'):
                try:
                    validate_source_shot(plan, shot)
                except ValueError as exc:
                    raise Paused('isolated_source_timing', {'shot_id': shot['id'],
                                 'source_path': shot['source_path'], 'reason': str(exc)}) from exc
        return
    for shot in plan['shots']:
        if shot.get('source_path') and shot.get('source_fit_policy') == 'whole_action':
            try:
                media.whole_action_filter(shot['source_path'], shot['duration_seconds'], shot.get('source_start', 0))
            except ValueError as exc:
                raise Paused('isolated_source_timing', {'shot_id':shot['id'],
                             'source_path':shot['source_path'], 'reason':str(exc)}) from exc


def run_product(item, config, output, through):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', item['product_id']):
        raise ValueError('Unsafe product ID')
    for context in item.get('audit_context', []):
        path = checked(context['path'], context['sha256'])
        finding = read(path)
        if context.get('blocks_registration') or finding.get('blocks_registration') or finding.get('status', '').startswith('isolated'):
            raise Paused('isolated_known_visual_findings', {'evidence_path': str(path),
                         'evidence_sha256': context['sha256'], 'reason': 'Unresolved prior findings; no paid downstream work allowed'})
    assets = complete_assets(item)
    validate_source_timing(read(item['manifest_path']))
    identity = digest({'item': item, 'assets': assets,
                       'destination': {k: config.get(k) for k in ('production_root', 'backend_file', 'renderer_runtime_path')}})
    folder = output / item['product_id'] / identity[:24]
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'downstream.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        observations = []
        for context in item.get('audit_context', []):
            path = checked(context['path'], context['sha256'])
            observations.append({**context, 'content': read(path)})
        if observations:
            write_json(folder / 'external_observations.json', {'items': observations,
                       'scope': 'Prior observations, not a substitute for the actual complete-board review'})
        binding_path = folder / 'binding.json'
        def bound_evidence():
            result = read(binding_path)
            checked(result['manifest_path'], result['manifest_sha256'])
            for asset in result['assets']:
                checked(asset['imported_path'], asset['sha256'])
            missing = storyboard.missing_previsuals(read(result['manifest_path']))
            if missing:
                raise ValueError('Incomplete bound board: ' + ', '.join(missing))
            return result
        binding = checkpoint(folder, 'bind', identity,
                             lambda: write_json(binding_path, bind({**item, 'assets': assets})), bound_evidence)
        manifest = Path(binding['manifest_path'])
        review_path = folder / 'narrative_review.json'
        row = {'product_id': item['product_id'], 'state': 'bound_review_pending', 'folder': str(folder),
               'manifest_path': str(manifest), 'manifest_sha256': sha256(manifest)}
        if observations:
            row['audit_context_path'] = str(folder / 'external_observations.json')
        if PHASES.index(through) < 1:
            return row
        checkpoint(folder, 'review', sha256(manifest), lambda: review_manifest(manifest, review_path),
                   lambda: require_review(manifest, review_path))
        row.update(state='reviewed_preflight_pending', review_path=str(review_path))
        if PHASES.index(through) < 2:
            return row
        plan = read(manifest)
        preflight = folder / 'preflight'
        preflight.mkdir(exist_ok=True)
        audio_receipt = preflight / 'audio.json'
        def produce_audio():
            voice = media.narration(plan, preflight / 'narration.wav')
            voice['audio_path'] = str((preflight / 'narration.wav').resolve())
            media.music(plan, preflight / 'music.wav')
            music = str((preflight / 'music.wav').resolve())
            write_json(audio_receipt, {'voice': voice, 'music_path': music,
                                     'voice_sha256': sha256(voice['audio_path']), 'music_sha256': sha256(music)})
        def audio_evidence():
            a = read(audio_receipt)
            checked(a['voice']['audio_path'], a['voice_sha256']); checked(a['music_path'], a['music_sha256'])
            return a
        audio = checkpoint(folder, 'audio', sha256(manifest), produce_audio, audio_evidence)
        acceptance_path = preflight / 'acceptance.json'
        try:
            checkpoint(folder, 'preflight', digest([sha256(manifest), sha256(audio_receipt)]),
                       lambda: write_json(acceptance_path, storyboard.preflight(plan, audio['voice'], {'audio_path': audio['music_path']}, preflight)),
                       lambda: verify_acceptance(acceptance_path))
        except Exception as exc:
            raise preflight_pause(preflight, exc)
        row.update(state='ready_for_staging', acceptance_path=str(acceptance_path))
        if any(c.get('blocks_registration') or c['content'].get('blocks_registration') for c in observations):
            row.update(state='isolated_known_visual_findings',
                       reason='Prior visual findings remain unresolved; model pass cannot silently override them')
            return row
        if PHASES.index(through) < 3:
            return row
        # Capability checks use the existing pool contract; one start image is
        # not silently duplicated into an unreviewed first/last-frame pair.
        try:
            request_checks = validate_generation_requests(plan, read(config['backend_file']))
            write_json(folder / 'generation_request_preflight.json',
                       {'status': 'passed', 'pure_function_only': True, 'video_submissions': 0,
                        'manifest_sha256': sha256(manifest), 'shots': request_checks})
        except Exception as exc:
            row.update(state='isolated_generation_contract', generation_error=str(exc))
            return row
        root = Path(config['production_root']).resolve()
        parent_path = folder / 'parent_binding.json'
        if not parent_path.exists():
            with sqlite3.connect('file:' + str(root / 'queue.sqlite') + '?mode=ro', uri=True) as db:
                parent = db.execute('select current_job from production_items where item_id=?', (item['product_id'],)).fetchone()
            if not parent or not parent[0]:
                raise ValueError('Original product card missing')
            if item.get('parent_job_id') and item['parent_job_id'] != parent[0]:
                raise ValueError('Requested parent differs from current product job')
            write_json(parent_path, {'parent_job_id': parent[0], 'product_id': item['product_id']})
        parent = read(parent_path)['parent_job_id']
        packet_path = folder / 'request_packet.json'
        def do_stage():
            result = stage(acceptance_path, item['product_id'], parent, root, source_identity_resolution=config.get('source_identity_resolution'))
            packet = {**result, 'backend_file': str(Path(config['backend_file']).resolve()),
                      'required_story_preflight_version': storyboard.PREFLIGHT_VERSION,
                      'required_media_version': 'whole_action_v1',
                      'required_story_review_version': 'shot-review-v3-explicit-endpoint-evidence',
                      'required_media_sha256': code_identity(PROJECT / 'workflow/media.py'),
                      'renderer_runtime_path': str(Path(config['renderer_runtime_path']).resolve())}
            from workflow.first_frame import enabled, runtime_requirements
            if enabled(plan):
                packet['required_first_frame_runtime']=runtime_requirements()
            write_json(packet_path, packet)
        def staged_evidence():
            packet = read(packet_path)
            for key in ('manifest', 'review', 'preflight'):
                checked(packet[key + '_path'], packet[key + '_sha256'])
            require_review(packet['manifest_path'], packet['review_path'])
            verify_acceptance(Path(packet['preflight_path']))
            return packet
        stage_name = 'stage' + ('_' + digest(config['source_identity_resolution'])[:16] if config.get('source_identity_resolution') else '')
        packet = checkpoint(folder, stage_name, digest([sha256(acceptance_path), parent, config.get('source_identity_resolution')]) if config.get('source_identity_resolution') else digest([sha256(acceptance_path), parent]), do_stage, staged_evidence)
        row.update(state='staged_ready_for_registration', packet_path=str(packet_path), packet_sha256=sha256(packet_path))
        try:
            runtime_ready(packet)
        except (Paused, FileNotFoundError, ProcessLookupError) as exc:
            row.update(state='staged_runtime_pending', runtime_error=str(exc))
            return row
        if through == 'register':
            # register() is itself locked/idempotent. This is an inbox write,
            # never a second controller, direct model call or queue admission.
            receipt = register(packet, root)
            write_json(folder / 'registration.json', receipt)
            row.update(state='awaiting_singleton_admission', registration=receipt)
        return row


def run(packet_path, output, through='collect'):
    config = read(packet_path)
    packet_checksum = sha256(packet_path)
    if config.get('schema_version') != 1 or not isinstance(config.get('products'), list):
        raise ValueError('Packet requires schema_version=1 and products list')
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    rows = []
    with (output / 'downstream_dispatch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for item in config['products']:
            try:
                checked(packet_path, packet_checksum)
                row = run_product(item, config, output, through)
            except Paused as exc:
                row = {'product_id': item['product_id'], 'state': exc.state, 'detail': exc.detail}
            except Exception as exc:
                row = {'product_id': item.get('product_id'), 'state': 'isolated_error', 'error_type': type(exc).__name__, 'error': str(exc)}
            rows.append(row)
            write_json(output / 'inventory.json', {'schema_version': 1, 'packet_path': str(Path(packet_path).resolve()),
                       'packet_sha256': packet_checksum, 'through': through, 'items': rows,
                       'video_submissions': 0, 'new_controllers': 0, 'updated_at': time.time()})
            print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows
