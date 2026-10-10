"""Immutable candidate packages, evidence checks and title-independent deduplication.

No queue/database, credential loading, model calls or accelerator submission.
All paths in manifests are relative to the dedicated material research root.
"""
import datetime as dt
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalized(value):
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', value).casefold())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _canonical_query(p):
    query = []
    for k, v in parse_qsl(p.query, keep_blank_values=True):
        if re.search(r'token|signature|credential|key|auth|cookie|session|secret|sig$', k, re.I):
            raise ValueError('credential/signed URL must not enter manifests')
        if not k.lower().startswith('utm_') and k not in ('spm', 'from', 'feature', 'si'):
            query.append((k, v))
    return query


def canonical_url(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError('public HTTPS source required')
    query = _canonical_query(p)
    return urlunsplit(('https', p.hostname.lower(), p.path or '/', urlencode(sorted(query)), ''))


def content_id(event):
    # Explicit event tuple is independent of title, source, crop and wording.
    for field in ('entity', 'action', 'date', 'object'):
        if not isinstance(event.get(field), str) or not normalized(event[field]):
            raise ValueError('event requires ' + field)
    dt.date.fromisoformat(event['date'])
    return 'material_' + digest({k: normalized(event[k]) for k in ('entity', 'action', 'date', 'object')})[:24]


def fact_id(fact):
    return digest({k: normalized(fact[k]) for k in ('subject', 'predicate', 'object', 'modality')})


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding="utf-8")
    temp.replace(path)


def local_path(root, value):
    if not isinstance(value, str) or not value or Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError('relative evidence path required')
    path = (Path(root) / value).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
        raise ValueError('missing or escaping evidence: ' + value)
    return path


def _validate_publication_date(source, body):
    if source.get('published'):
        evidence = source.get('date_evidence', '')
        dates = re.findall(r'(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})', evidence)
        normalized_dates = set()
        for parts in dates:
            try:
                normalized_dates.add(dt.date(*map(int, parts)).isoformat())
            except ValueError:
                pass
        # Preserve the source's visible English date rather than adding a
        # synthetic ISO date to the archived article text.
        months={name:i for i,name in enumerate(('jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'),1)}
        for month,day,year in re.findall(r'\b([A-Za-z]{3,9})\s+(\d{1,2}),\s*(20\d{2})\b',evidence):
            try:normalized_dates.add(dt.date(int(year),months[month[:3].lower()],int(day)).isoformat())
            except (ValueError,KeyError):pass
        # arXiv prints its version date as "02 Oct 2026". Keep that
        # exact visible evidence rather than rewriting the archived body.
        for day,month,year in re.findall(r'\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})\b',evidence):
            try:normalized_dates.add(dt.date(int(year),months[month[:3].lower()],int(day)).isoformat())
            except (ValueError,KeyError):pass
        if not evidence or evidence not in body or source['published'] not in normalized_dates:
            raise ValueError('publication date lacks exact visible evidence')


def _validate_sources(package, root):
    sources = {}
    for source in package['sources']:
        if source['id'] in sources:
            raise ValueError('duplicate source id')
        canonical_url(source['url'])
        if source.get('published'):
            dt.date.fromisoformat(source['published'])
        for field, hashfield in [('archive_path', 'sha256'), ('text_path', 'text_sha256')]:
            if sha256(local_path(root, source[field])) != source[hashfield]:
                raise ValueError('source hash mismatch')
        body = local_path(root, source['text_path']).read_text(encoding="utf-8")
        if source.get('publication_index'):
            from .publication_index import validate as validate_index
            validate_index(source, root)
        else:
            _validate_publication_date(source, body)
        sources[source['id']] = source
    return sources


def _validate_facts(package, root, sources):
    facts = set()
    for fact in package['facts']:
        if fact['id'] in facts:
            raise ValueError('duplicate fact id')
        facts.add(fact['id'])
        if fact['modality'] not in ('observed', 'reported', 'planned', 'hypothesis'):
            raise ValueError('explicit fact modality required')
        if fact.get('fingerprint') != fact_id(fact):
            raise ValueError('fact fingerprint mismatch')
        if not fact.get('evidence'):
            raise ValueError('fact needs source evidence')
        for evidence in fact['evidence']:
            source = sources[evidence['source_id']]
            body = local_path(root, source['text_path']).read_text(encoding="utf-8")
            quote = evidence['quote']
            if not quote or len(quote) > 200 or body[evidence['start']:evidence['end']] != quote:
                raise ValueError('evidence span mismatch')
    return facts


def _validate_asset_rights_duration(asset, root):
    if asset['rights']['status'] not in ('unknown', 'restricted', 'licensed', 'public_domain', 'original'):
        raise ValueError('invalid rights status')
    if asset['rights'].get('evidence_path'):
        local_path(root, asset['rights']['evidence_path'])
    if type(asset['duration']) not in (int, float) or not math.isfinite(asset['duration']) or asset['duration'] < 0:
        raise ValueError('invalid asset duration')


def _validate_assets(package, root, sources):
    assets = {}
    for asset in package['assets']:
        canonical_url(asset['url'])
        path = local_path(root, asset['path'])
        if asset['id'] in assets or sha256(path) != asset['sha256'] or path.stat().st_size != asset['bytes']:
            raise ValueError('asset id/hash mismatch')
        if asset['source_id'] not in sources:
            raise ValueError('asset source missing')
        _validate_asset_rights_duration(asset, root)
        assets[asset['id']] = asset
    return assets


def _validate_shot_evidence(shot, start, end, root):
    if shot['status'] not in ('candidate', 'visually_reviewed'):
        raise ValueError('invalid shot status')
    if shot['status'] == 'visually_reviewed' and not shot.get('observation'):
        raise ValueError('reviewed shot requires observation')
    if not shot['keyframes']:
        raise ValueError('shot requires frame evidence')
    for frame in shot['keyframes']:
        if not start <= frame['at'] < end or sha256(local_path(root, frame['path'])) != frame['sha256']:
            raise ValueError('invalid frame evidence')

def _validate_shots(package, root, assets):
    previous = {}
    shot_ids = set()
    for shot in package['shots']:
        asset = assets[shot['asset_id']]
        start, end = shot['start'], shot['end']
        if shot['id'] in shot_ids or not all(type(x) in (int, float) and math.isfinite(x) for x in (start, end)):
            raise ValueError('duplicate shot/nonfinite time')
        shot_ids.add(shot['id'])
        if not previous.get(asset['id'], 0) <= start < end <= asset['duration'] + .001:
            raise ValueError('overlapping/out-of-range shot')
        previous[asset['id']] = end
        _validate_shot_evidence(shot, start, end, root)



def _validate_transcript(package, root, assets):
    transcript = package['transcript']
    if transcript['status'] not in ('pending', 'not_applicable', 'automatic_unreviewed', 'reviewed'):
        raise ValueError('invalid transcript status')
    if transcript['status'] in ('automatic_unreviewed', 'reviewed'):
        path = local_path(root, transcript['path'])
        if sha256(path) != transcript['sha256']:
            raise ValueError('transcript hash mismatch')
        rows = json.loads(path.read_text(encoding="utf-8"))['segments']
        duration = assets[transcript['asset_id']]['duration']
        if any(not all(type(x) in (int, float) and math.isfinite(x) for x in (r['start'], r['end']))
               or not 0 <= r['start'] < r['end'] <= duration + .1 for r in rows):
            raise ValueError('transcript timecode out of range')


def validate(package, root):
    if package.get('schema_version') != 1:
        raise ValueError('schema_version must be 1')
    if package.get('content_id') != content_id(package['event']):
        raise ValueError('content_id does not match event')
    if not package.get('title') or not package.get('sources') or not package.get('facts'):
        raise ValueError('title, sources and facts required')
    sources = _validate_sources(package, root)
    facts = _validate_facts(package, root, sources)
    assets = _validate_assets(package, root, sources)
    _validate_shots(package, root, assets)
    _validate_transcript(package, root, assets)
    for beat in package['editorial']['beats']:
        if not set(beat['fact_ids']).issubset(facts):
            raise ValueError('editorial references unknown fact')
    return package


def _grams(text):
    text = normalized(text)
    return {text[i:i+3] for i in range(max(0, len(text)-2))}


def _duplicate_reasons(old, package, fingerprints, asset_hashes, urls, grams):
    reasons = []
    if old.get('content_id') == package['content_id']:
        reasons.append('same_event')
    if fingerprints & set(old.get('fact_fingerprints', [])):
        reasons.append('same_canonical_fact')
    if asset_hashes & set(old.get('asset_sha256', [])):
        reasons.append('same_media_bytes_requires_editorial_review')
    if urls & set(old.get('urls', [])):
        reasons.append('same_source_requires_editorial_review')
    other = _grams(old.get('fact_text', ''))
    overlap = len(grams & other) / max(1, min(len(grams), len(other)))
    if overlap >= .7:
        reasons.append('fact_text_overlap_requires_editorial_review')
    return reasons, overlap


def _append_rights_blockers(package, blockers):
    for asset in package['assets']:
        if asset['use'] == 'production' and (asset['rights']['status'] not in ('licensed', 'public_domain', 'original')
                                           or not asset['rights'].get('evidence_path')):
            blockers.append('rights_' + asset['id'])


def _research_ready(package, collection):
    research = (collection and all(f['verification'] == 'source_supported' for f in package['facts'])
                and all(s['published'] and s['date_evidence'] for s in package['sources']))
    return research


def deduplicate(package, existing):
    hits = []
    urls = {canonical_url(s['url']) for s in package['sources']}
    fingerprints = {f['fingerprint'] for f in package['facts']}
    asset_hashes = {a['sha256'] for a in package.get('assets', [])}
    grams = _grams(''.join(f['text'] for f in package['facts']))
    for old in existing:
        reasons,overlap=_duplicate_reasons(old,package,fingerprints,asset_hashes,urls,grams)
        if reasons:
            hits.append({'reference': old['reference'], 'reasons': reasons,
                         'fact_overlap': round(overlap, 3)})
    return {'status': 'review_required' if hits else 'clear_in_scanned_scope',
            'checked_records': len(existing), 'matches': hits,
            'scope': 'Local catalogs and file inbox only; no global semantic uniqueness guarantee.'}


def legacy_records(paths):
    rows = []
    for path in sorted(map(Path, paths)):
        value = json.loads(path.read_text(encoding="utf-8"))
        topics = value.get('topics', []) if isinstance(value, dict) else []
        for topic in topics:
            url = topic.get('source_url')
            rows.append({'reference': str(path) + '#' + str(topic.get('id', '')),
                         'urls': [canonical_url(url)] if url else [],
                         'fact_text': ''.join(f.get('text', '') for f in topic.get('facts', []))})
    return rows


def readiness(package, dedup):
    collection = not package['collection_pending']
    research = _research_ready(package, collection)
    blockers = list(package['production_pending'])
    if not research:
        blockers.append('research_evidence_incomplete')
    if dedup['status'] != 'clear_in_scanned_scope':
        blockers.append('dedup_editorial_review')
    if not package['shots'] or any(s['status'] != 'visually_reviewed' for s in package['shots']):
        blockers.append('complete_shot_review')
    if package['transcript']['status'] not in ('reviewed', 'not_applicable'):
        blockers.append('transcript_review')
    _append_rights_blockers(package, blockers)
    if not package.get('production_asset_plan_reviewed'):
        blockers.append('production_asset_plan_review')
    return {'collection_complete': collection, 'research_ready': research,
            'production_ready': research and not blockers,
            'production_approved': False, 'queue_jobs_created': 0,
            'blockers': sorted(set(blockers))}


def enqueue_candidate(package, root, existing=()):
    root = Path(root).resolve()
    validate(package, root)
    # Exclusive creation: rerun is idempotent; a different version never overwrites.
    folder = root / 'inbox' / package['content_id']
    prior = list(existing)
    for p in sorted((root / 'inbox').glob('*/package.json')):
        if p.parent == folder:
            continue
        old = json.loads(p.read_text(encoding="utf-8"))
        prior.append({'reference': str(p), 'content_id': old['content_id'],
                      'urls': [canonical_url(s['url']) for s in old['sources']],
                      'fact_fingerprints': [f['fingerprint'] for f in old['facts']],
                      'asset_sha256': [a['sha256'] for a in old['assets']],
                      'fact_text': ''.join(f['text'] for f in old['facts'])})
    dedup = deduplicate(package, prior)
    receipt = {'content_id': package['content_id'], 'package_sha256': digest(package),
               'dedup': dedup, 'readiness': readiness(package, dedup),
               'state': 'candidate_only', 'entrypoint': 'file_inbox'}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'package.json'
    try:
        with path.open('x') as f:
            f.write(json.dumps(package, ensure_ascii=False, indent=2) + '\n')
    except FileExistsError:
        if digest(json.loads(path.read_text(encoding="utf-8"))) != digest(package):
            raise ValueError('immutable content_id already exists; explicit revision required')
        if not (folder / 'receipt.json').exists():
            # Recover interrupted receipt write without changing the package.
            write_json(folder / 'receipt.json', receipt)
        return json.loads((folder / 'receipt.json').read_text(encoding="utf-8"))
    write_json(folder / 'receipt.json', receipt)
    return receipt
