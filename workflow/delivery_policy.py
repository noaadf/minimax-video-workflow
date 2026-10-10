"""Delivery gates and local, immutable product-card delivery storage."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from .core.io import sha256, write_json

PROFILE='throughput_v1'
CATEGORIES = ('fact', 'geometry', 'visual_quality', 'visual_repetition',
              'visual_text_mismatch', 'audio', 'subtitle', 'unknown', 'engagement', 'speech')
SPEECH_STATUSES = ('transcript_match', 'homophone_spelling_only', 'phonetic_review_required',
                   'missing_transcript', 'speech_not_assessed', 'speech_deferred')


def valid_review(review):
    """Only recognized verdicts and attributable findings reach the policy."""
    return (isinstance(review, dict)
            and review.get('verdict') in ('pass', 'review', 'reject')
            and isinstance(review.get('findings'), list)
            and all(isinstance(f, dict)
                    and f.get('category') in CATEGORIES
                    and f.get('severity') in ('low', 'medium', 'high')
                    and isinstance(f.get('issue'), str) and f['issue'].strip()
                    for f in review['findings']))

def is_blocking(finding):
    category=finding.get('category');severity=finding.get('severity')
    return (category in ('fact','geometry') and severity in ('medium','high') or
            category in ('visual_quality','visual_text_mismatch','audio','subtitle','unknown') and severity=='high')


def assess(quality):
    blockers=[];advisories=[]
    # Owner directive 2026-10-06 (审核证据这一关放松): review-EVIDENCE availability is
    # advisory, not delivery-blocking. Technical media QC, speech screening and
    # material findings (fact/geometry/high-severity defects) still block.
    if quality.get('storyboard_required') and quality.get('shot_review',{}).get('status') != 'sampled_checks_passed':
        advisories.append({'category':'story_action_unverified','issue':'Shot action/result checks not all passed (owner-relaxed 2026-10-06)'})
    if quality.get('passed') is not True:blockers.append({'category':'technical','issue':'Required media checks failed'})
    editorial=quality.get('editorial',{})
    review=editorial.get('review') if isinstance(editorial,dict) else None
    if not isinstance(review,dict) or not editorial.get('receipt'):
        advisories.append({'category':'review_unavailable','issue':'No completed initial content check (owner-relaxed 2026-10-06)'})
    elif not valid_review(review):
        advisories.append({'category':'invalid_review','issue':'Content review evidence incomplete (owner-relaxed 2026-10-06)'})
    else:
        for f in review.get('findings',[]):
            (blockers if is_blocking(f) else advisories).append(f)
        if review.get('verdict')=='reject' and not blockers:
            advisories.append({'category':'unresolved_rejection','issue':'Model rejected without an attributable material finding (owner-relaxed 2026-10-06)'})
    speech=quality.get('speech',{})
    if 'speech' in quality and (not isinstance(speech,dict) or speech.get('status') not in SPEECH_STATUSES):
        blockers.append({'category':'audio_check_unavailable','issue':'Transcription record is incomplete or has an unrecognized status; required screening has not completed'})
    elif speech.get('status')=='missing_transcript':blockers.append({'category':'audio','issue':'No recognized speech'})
    elif speech.get('status') in ('speech_not_assessed','speech_deferred'):
        blockers.append({'category':'audio_check_unavailable','issue':'Required transcription has not completed; not a confirmed voice defect'})
    elif speech.get('review_required'):advisories.append({'category':'audio','issue':'ASR uncertainty retained; not a confirmed audible defect'})
    return {'profile':PROFILE,'ready_for_delivery':not blockers,'blockers':blockers,'advisories':advisories,
            'max_material_repairs':1,'cosmetic_repairs':0,'publication_approved':False,
            'scope':'Delivery under user throughput policy; not a measured90/95percent score or social publication approval.'}


def assess_strict(quality):
    """Mirror the existing strict editorial status gate, preserving uncertainty.

    This does not apply throughput exceptions to a strict film. The reviewer is
    the authority for automatic_review_passed (including its confidence rules);
    no new numeric threshold or finding-severity policy is introduced here.
    """
    result=assess(quality)
    result['profile']='strict_v1'
    editorial=quality.get('editorial',{})
    review=editorial.get('review',{}) if isinstance(editorial,dict) else {}
    adjudicated=(isinstance(editorial,dict) and editorial.get('status')=='supervisor_adjudicated'
        and quality.get('editorial_status')=='supervisor_adjudicated'
        and review.get('verdict') in ('pass','review'))
    if (not isinstance(editorial,dict) or
        (editorial.get('status')!='automatic_review_passed' and not adjudicated) or
        (quality.get('editorial_status')!='automatic_review_passed' and not adjudicated) or
        (review.get('verdict')!='pass' and not adjudicated)):
        result['advisories'].append({'category':'strict_review_pending','issue':'Strict editorial approval not completed (owner-relaxed 2026-10-06)'})
    receipt=editorial.get('receipt') if isinstance(editorial,dict) else None
    if not isinstance(receipt,dict) or not receipt.get('model'):
        result['advisories'].append({'category':'review_unavailable','issue':'Model receipt not attributable (owner-relaxed 2026-10-06)'})
    if 'speech' not in quality:
        result['blockers'].append({'category':'audio_check_unavailable','issue':'Required speech screening receipt missing'})
    result['ready_for_delivery']=not result['blockers']
    result['scope']='Strict automatic review readiness only; full-shot/audio/SHA delivery validation and user approval remain separate.'
    return result


def resolve(quality,plan):
    """Derive a fresh gate from original evidence; never mutate old receipts."""
    return assess(quality) if plan.get('quality_profile')==PROFILE else assess_strict(quality)


def gate_state(quality,policy):
    """Unavailable checks mean pending review, not an invented visual defect."""
    if policy['ready_for_delivery']:return 'ready'
    review=quality.get('editorial',{}).get('review',{})
    material=any(is_blocking(f) for f in review.get('findings',[]) if isinstance(f,dict))
    shots=quality.get('shot_review',{}).get('shots',[])
    material=material or any(r.get('review',{}).get('verdict')=='reject' or
        any(is_blocking(f) for f in r.get('review',{}).get('findings',[]) if isinstance(f,dict)) for r in shots)
    if quality.get('passed') is False or review.get('verdict')=='reject' or material:
        return 'failed'
    return 'awaiting_review'


def safe_component(value):
    """Identifiers are single path components, never paths or dot entries."""
    if not isinstance(value, str) or not re.fullmatch(r'[\w-]{1,240}', value):
        raise ValueError('Unsafe product/job identifier')
    return value


def delivery_filename(title, product):
    title = re.sub(r'[^\w -]', '_', str(title))[:64].strip(' ._-') or '视频'
    return f'{title}_{safe_component(product)[:8]}.mp4'


def _directory(path, boundary):
    """Do not follow an existing directory symlink into another writer's area."""
    path, boundary = Path(path), Path(boundary)
    if not path.is_relative_to(boundary):
        raise ValueError('Destination outside card workspace')
    for part in (path, *path.parents):
        if part == boundary.parent:
            break
        if part.is_symlink():
            raise ValueError(f'Symlink destination: {part}')
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def card_lock(root):
    root = Path(root).resolve()
    runs = _directory(root / 'output/production/runs', root)
    with (runs / '.product-cards.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _copy_snapshot(source, target, checksum=None, *, recover_partial=False):
    source, target = Path(source), Path(target)
    expected = checksum or sha256(source)
    if target.is_symlink():
        raise ValueError('Snapshot target is a symlink')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256(target) != expected:
            if not recover_partial:
                raise ValueError(f'Existing snapshot changed: {target}')
            target.rename(target.with_name('.interrupted-' + uuid.uuid4().hex + '-' + target.name))
    if not target.exists():
        # An interrupted copy is retained. It must be inspected, never erased.
        shutil.copyfile(source, target)
    if sha256(target) != expected or sha256(source) != expected:
        raise ValueError(f'Snapshot SHA mismatch: {source}')
    return expected


def _publish_flat(root, card, entry):
    exports = _directory(root / 'exports', root)
    index_path = exports / 'delivery_index.json'
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {'schema_version': 1, 'items': []}
    items = {row['product_id']: row for row in index['items']}
    product = card['product_id']
    previous = items.get(product)
    for row in items.values():
        if Path(row['filename']).name != row['filename'] or not row['filename'].endswith('.mp4'):
            raise ValueError('Unsafe indexed delivery filename')
    name = delivery_filename(card['title'], product)
    occupied = {row['filename']: pid for pid, row in items.items()}
    if name in occupied and occupied[name] != product:
        # Legacy IDs share prefixes (production_, pilot6_); preserve uniqueness.
        name = name[:-4] + '_' + hashlib.sha256(product.encode()).hexdigest()[:8] + '.mp4'
    if name in occupied and occupied[name] != product:
        raise ValueError('Delivery filename collision')
    final = exports / name
    if final.is_symlink():
        raise ValueError('Flat delivery is a symlink')
    version = root / 'output/production/runs' / product / entry['directory']
    if final.exists() and sha256(final) != entry['video_sha256']:
        if not previous or previous['filename'] != name or sha256(final) != previous['video_sha256']:
            raise ValueError('Unindexed or changed flat delivery; preserve it for inspection')
        archive = _directory(root / 'archives/exports_flat_20261003' / product / 'flat', root)
        final.rename(archive / f'{time.time_ns()}_{name}')
    if not final.exists():
        pending = version / '.delivery-copy.mp4'
        _copy_snapshot(version / 'video.mp4', pending, entry['video_sha256'], recover_partial=True)
        pending.rename(final)
    if previous and previous['filename'] != name:
        old = exports / previous['filename']
        if old.exists():
            if old.is_symlink() or sha256(old) != previous['video_sha256']:
                raise ValueError('Previous flat delivery changed')
            archive = _directory(root / 'archives/exports_flat_20261003' / product / 'flat', root)
            old.rename(archive / f'{time.time_ns()}_{old.name}')
    delivered_at = (previous['delivered_at'] if previous and
                    previous.get('version') == entry['version'] and
                    previous.get('video_sha256') == entry['video_sha256'] else time.time())
    row = {'title': card['title'], 'product_id': product, 'version': entry['version'],
           'version_job_id': entry['job_id'], 'video_sha256': entry['video_sha256'],
           'filename': name, 'path': str(final), 'delivered_at': delivered_at,
           'publication_approved': False}
    items[product] = row
    index.update(updated_at=time.time(), count=len(items), items=sorted(items.values(), key=lambda r: r['product_id']))
    write_json(index_path, index)
    return row


def _delivery_sources(verified, manifest, job, checksum, deliver):
    sources = dict(verified['sources'])
    if 'video.mp4' not in sources or sha256(sources['video.mp4']) != checksum:
        raise ValueError('Validated video changed')
    if deliver and not {'video.mp4', 'cover.png', 'publication.txt'} <= sources.keys():
        raise ValueError('Delivery assets are incomplete')
    if manifest.get('id') and manifest['id'] != job:
        raise ValueError('Manifest belongs to another job')
    return sources


def _delivery_evidence(verified):
    evidence = {}
    for key, value in verified.get('evidence', {}).items():
        if key.endswith('_sha256') or not isinstance(value, str):
            continue
        path = Path(value)
        if not path.is_file():
            raise ValueError(f'Missing evidence: {value}')
        expected = verified['evidence'].get(key + '_sha256')
        actual = sha256(path)
        if expected and actual != expected:
            raise ValueError(f'Evidence changed: {key}')
        evidence[key] = {'source': str(path.resolve()), 'sha256': actual,
                         'path': 'evidence/' + safe_component(key) + path.suffix}
    return evidence


def _product_card(card_path, product, verified):
    card = json.loads(card_path.read_text(encoding="utf-8")) if card_path.exists() else {
        'schema_version': 1, 'product_id': product, 'title': verified.get('title', product),
        'created_at': time.time(), 'current_version': None, 'versions': []}
    if card.get('product_id') != product or card.get('schema_version') != 1:
        raise ValueError('Conflicting product card')
    for recorded in card['versions']:
        if (type(recorded['version']) is not int or recorded['version'] < 1 or
                recorded['directory'] != f"versions/{recorded['version']}"):
            raise ValueError('Unsafe card version pointer')
    return card


def _recover_unrecorded_version(card, versions, job, checksum, acceptance_class, existing):
    # Recover a directory rename committed just before card.json was saved.
    if existing is None:
        recorded_numbers = {v['version'] for v in card['versions']}
        for path in sorted(versions.glob('[0-9]*/version.json')):
            orphan = json.loads(path.read_text(encoding="utf-8"))
            if (orphan['version'] not in recorded_numbers and orphan['job_id'] == job and
                orphan['video_sha256'] == checksum and orphan['acceptance']['conclusion'] == acceptance_class):
                if orphan['directory'] != f"versions/{orphan['version']}":
                    raise ValueError('Invalid recovery version pointer')
                existing = orphan
                card['versions'].append(orphan)
                break
    return existing


def _verify_replay_sources(version, entry, sources, evidence, manifest, deliver):
    for name, source in sources.items():
        if name not in entry['files'] or sha256(source) != entry['files'][name]['sha256']:
            raise ValueError('Replay assets changed')
    if {key: value['sha256'] for key, value in evidence.items()} != {
            key: value['sha256'] for key, value in entry['evidence'].items()}:
        raise ValueError('Replay evidence changed')
    stored = json.loads((version / 'manifest.json').read_text(encoding="utf-8"))
    if stored != manifest:
        raise ValueError('Replay manifest changed')
    # Historical evidence must not become accepted merely by replaying it.
    if deliver and not entry['acceptance'].get('ready_for_delivery'):
        raise ValueError('Historical version needs explicit acceptance reconciliation')


def _snapshot_files(root, staging, sources, extra_files, checksum, evidence, manifest):
    files = {}
    for name, source in {**sources, **(extra_files or {})}.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise ValueError('Unsafe snapshot filename')
        if name in ('manifest.json', 'version.json'):
            raise ValueError('Reserved snapshot filename')
        _directory((staging / relative).parent, root)
        files[name] = {'sha256': _copy_snapshot(source, staging / name,
                              checksum if name == 'video.mp4' else None, recover_partial=True)}
    for meta in evidence.values():
        files[meta['path']] = {'sha256': _copy_snapshot(meta['source'], staging / meta['path'], meta['sha256'], recover_partial=True)}
    write_json(staging / 'manifest.json', manifest)
    files['manifest.json'] = {'sha256': sha256(staging / 'manifest.json')}
    return files


def _delivery_receipt(verified, entry, card_path, version, delivery):
    receipt = {k: v for k, v in verified.items() if k != 'sources'}
    receipt.update(version=entry['version'], card_path=str(card_path),
                   files={n: {'path': str(version / n), **m} for n, m in entry['files'].items()},
                   exported_at=delivery['delivered_at'] if delivery else None,
                   flat_delivery=delivery)
    return receipt


def store_product_version(root, verified, manifest, *, repair_reason='',
                          make_current=True, deliver=True, extra_files=None):
    """Snapshot local evidence, append history, then replayable current/export writes.

    Call only with an existing validated delivery receipt. No review, API, queue,
    or production-state mutation occurs here. Historical migration uses both
    make_current=False and deliver=False; it cannot grant delivery readiness.
    """
    root = Path(root).resolve()
    product = safe_component(verified['product_id'])
    job = safe_component(verified['version_job_id'])
    checksum = verified['video_sha256']
    if not re.fullmatch(r'[0-9a-f]{64}', checksum):
        raise ValueError('A video SHA256 is required')
    policy = verified.get('delivery_policy', {})
    if deliver and (not make_current or verified.get('system_ready') is not True or
                    policy.get('ready_for_delivery') is not True or policy.get('blockers')):
        raise ValueError('Delivery requires a validated, unblocked current version')
    sources = _delivery_sources(verified, manifest, job, checksum, deliver)
    # Prepare evidence hashes before any card mutation.
    evidence = _delivery_evidence(verified)
    with card_lock(root):
        folder = _directory(root / 'output/production/runs' / product, root)
        versions = _directory(folder / 'versions', root)
        card_path = folder / 'card.json'
        card = _product_card(card_path, product, verified)
        acceptance_class = 'validated_delivery' if deliver else 'historical_unverified'
        existing = next((v for v in card['versions'] if v['job_id'] == job and
                         v['video_sha256'] == checksum and
                         v['acceptance']['conclusion'] == acceptance_class), None)
        existing = _recover_unrecorded_version(card, versions, job, checksum, acceptance_class, existing)
        if existing:
            entry = existing
            version = folder / entry['directory']
            if version.is_symlink():
                raise ValueError('Immutable version directory is a symlink')
            for name, meta in entry['files'].items():
                if Path(name).is_absolute() or '..' in Path(name).parts:
                    raise ValueError('Unsafe immutable file pointer')
                if not (version / name).is_file() or sha256(version / name) != meta['sha256']:
                    raise ValueError('Immutable version changed')
            _verify_replay_sources(version, entry, sources, evidence, manifest, deliver)
        else:
            reserved = [int(p.name) for p in versions.iterdir() if p.name.isdigit()]
            number = max([v['version'] for v in card['versions']] + reserved + [0]) + 1
            version = versions / str(number)
            # Stable staging path permits replay after interruption, without removal.
            staging = _directory(versions / ('.pending-' + hashlib.sha256((job + checksum + acceptance_class).encode()).hexdigest()[:24]), root)
            files = _snapshot_files(root, staging, sources, extra_files, checksum, evidence, manifest)
            entry = {'version': number, 'directory': f'versions/{number}', 'job_id': job,
                     'parent_job_id': manifest.get('parent_job_id'), 'video_sha256': checksum,
                     'repair_reason': repair_reason or manifest.get('repair_reason') or '初版／历史导入（未记录返修原因）',
                     'acceptance': {'ready_for_delivery': bool(deliver),
                                    'conclusion': 'validated_delivery' if deliver else 'historical_unverified',
                                    'delivery_policy': policy, 'publication_approved': False},
                     'created_at': verified.get('created_at', time.time()),
                     'recorded_at': time.time(), 'evidence': evidence, 'files': files,
                     'source_exports': verified.get('source_exports', [])}
            write_json(staging / 'version.json', entry)
            staging.rename(version)
            card['versions'].append(entry)
        if make_current and (card['current_version'] is None or entry['version'] >= card['current_version']):
            if not entry['acceptance'].get('ready_for_delivery'):
                raise ValueError('Unverified history cannot become current')
            card['current_version'] = entry['version']
            card['title'] = verified.get('title', card['title'])
        card['updated_at'] = time.time()
        write_json(card_path, card)
        delivery = None
        if card['current_version'] == entry['version']:
            current = folder / 'current'
            if current.exists() and not current.is_symlink():
                raise ValueError('Existing current path must be preserved')
            pending_link = folder / ('.current-' + uuid.uuid4().hex)
            pending_link.symlink_to(entry['directory'])
            os.replace(pending_link, current)
            if deliver:
                delivery = _publish_flat(root, card, entry)
        receipt = _delivery_receipt(verified, entry, card_path, version, delivery)
        return receipt
