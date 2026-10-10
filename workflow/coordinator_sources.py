"""SHA-bound local derived clips. Builders may not submit provider requests."""
import json
import subprocess
import sys
from pathlib import Path

from workflow.core.io import digest, sha256, write_json


def validate_builders(spec, plan):
    shots = {s['id']: s for s in plan['shots']}
    seen = set()
    for b in spec.get('source_builders', []):
        target = b['shot_id']
        if target in seen or target not in shots or not shots[target].get('source_path'):
            raise ValueError('Source builder requires unique declared source shot')
        seen.add(target)
        if not Path(b['builder_path']).is_file() or sha256(b['builder_path']) != b['builder_sha256']:
            raise ValueError('Source builder changed')
        if not b.get('depends_on'):
            raise ValueError('Source builder must declare image dependencies')
        for dep in b['depends_on']:
            if dep['shot_id'] not in shots or dep['role'] not in ('start', 'end'):
                raise ValueError('Unknown source image dependency')
            if shots[dep['shot_id']].get('source_path'):
                raise ValueError('Source builders depend on images, not other source builders')


def build_sources(spec, plan, tasks, work):
    from workflow.storyboard_downstream import checkpoint, checked, Paused
    from workflow.media import probe
    validate_builders(spec, plan)
    shots = {s['id']: s for s in plan['shots']}
    receipts = []
    for b in spec.get('source_builders', []):
        inputs = []
        for dep in b['depends_on']:
            shot = shots[dep['shot_id']]
            refs = shot.get('reference_images', [])
            index = 0 if dep['role'] == 'start' else 1
            if len(refs) > index and Path(refs[index]).is_file():
                path = refs[index]; checksum = sha256(path)
            else:
                matches = [t for t in tasks if t['shot_id'] == dep['shot_id'] and t['role'] == dep['role'] and t['state'] == 'completed']
                if len(matches) != 1:
                    raise Paused('awaiting_source_dependency', {'shot_id': b['shot_id'], 'dependency': dep})
                path, checksum = matches[0]['image_path'], matches[0]['image_sha256']
            checked(path, checksum)
            inputs.append({**dep, 'path': path, 'sha256': checksum})
        folder = work / 'derived_sources' / digest(b)[:24]
        folder.mkdir(parents=True, exist_ok=True)
        output = Path(shots[b['shot_id']]['source_path']).resolve()
        receipt = folder / 'receipt.json'
        identity = digest([b, inputs, str(output)])
        def evidence():
            r = json.loads(receipt.read_text(encoding="utf-8"))
            if r['input_digest'] != identity:
                raise ValueError('Derived source identity changed')
            checked(r['path'], r['sha256'])
            return r
        def operation():
            if output.exists():
                raise ValueError('Unreceipted source output exists; refusing overwrite')
            output.parent.mkdir(parents=True, exist_ok=True)
            input_path = folder / 'inputs.json'; write_json(input_path, inputs)
            with (folder / 'builder.log').open('wb') as log:
                subprocess.run([sys.executable, str(checked(b['builder_path'], b['builder_sha256'])),
                                '--inputs-json', str(input_path), '--output', str(output)],
                               stdout=log, stderr=subprocess.STDOUT, timeout=120, check=True)
            meta = probe(output)
            if not any(s.get('codec_type') == 'video' for s in meta.get('streams', [])) or float(meta.get('format', {}).get('duration', 0)) <= 0:
                raise ValueError('Derived source is not a usable video')
            write_json(receipt, {'input_digest': identity, 'path': str(output), 'sha256': sha256(output),
                                'inputs': inputs, 'builder_path': b['builder_path'], 'builder_sha256': b['builder_sha256']})
        receipts.append(checkpoint(folder, 'build', identity, operation, evidence))
    write_json(work / 'derived_sources.json', receipts)
    return receipts


def source_preview(receipt, shot, folder):
    """Extract a real source-window first frame without rebuilding the source."""
    from workflow.storyboard_downstream import checkpoint, checked
    from PIL import Image
    offset = float(shot.get('source_start', 0))
    if offset < 0:
        raise ValueError('Negative source preview offset')
    checked(receipt['path'], receipt['sha256'])
    identity = digest([receipt['sha256'], offset, 'source-preview-v1'])
    folder = Path(folder) / identity[:24]
    folder.mkdir(parents=True, exist_ok=True)
    path, record = folder / 'first.png', folder / 'receipt.json'
    def evidence():
        r = json.loads(record.read_text(encoding="utf-8"))
        if r['input_digest'] != identity:
            raise ValueError('Source preview identity changed')
        checked(r['source_path'], r['source_sha256'])
        checked(r['path'], r['sha256'])
        with Image.open(r['path']) as im:
            im.verify()
        return r
    def operation():
        if path.exists():
            raise ValueError('Unreceipted source preview exists')
        subprocess.run(['ffmpeg', '-v', 'error', '-i', receipt['path'], '-ss', str(offset),
                        '-frames:v', '1', str(path)], check=True, timeout=60)
        with Image.open(path) as im:
            dimensions = list(im.size); im.verify()
        write_json(record, {'input_digest': identity, 'path': str(path.resolve()),
                            'sha256': sha256(path), 'source_path': receipt['path'],
                            'source_sha256': receipt['sha256'], 'source_start': offset,
                            'dimensions': dimensions, 'production_approved': False})
    return checkpoint(folder, 'extract', identity, operation, evidence)


def prepare_source_candidate(spec, plan, tasks, work):
    """Return an immutable asset-bound item; its new SHA still needs normal review.

    Keeps original ImageJobs and reviewed manifest intact. All generated refs are
    bound here as well so downstream does not look for jobs under the new SHA.
    """
    import copy
    from workflow.storyboard_downstream import complete_assets, checked
    from workflow.narrative_gate import require_review
    work = Path(work)
    if not spec.get('source_builders'):
        return dict(spec)
    checked(spec['manifest_path'], spec['manifest_sha256'])
    if json.loads(Path(spec['manifest_path']).read_text(encoding="utf-8")) != plan:
        raise ValueError('Source candidate plan differs from reviewed manifest')
    require_review(spec['manifest_path'], spec['narrative_review_path'])
    item = {**spec, 'image_jobs_dir': str(work / 'images')}
    assets = complete_assets(item)
    receipts = build_sources(spec, plan, tasks, work)
    candidate = copy.deepcopy(plan)
    shots = {s['id']: s for s in candidate['shots']}
    for asset in assets:
        shot = shots[asset['shot_id']]
        refs = list(shot.get('reference_images', []))
        if asset['role'] == 'start':
            shot['previsual_path'] = asset['path']; refs = [asset['path']] + refs[1:]
        else:
            if not refs:
                raise ValueError('End reference without start')
            refs = [refs[0], asset['path']]
        shot['reference_images'] = refs
    previews = []
    for builder, receipt in zip(spec['source_builders'], receipts):
        shot = shots[builder['shot_id']]
        preview = source_preview(receipt, shot, work / 'source_previews')
        shot.update(source_sha256=receipt['sha256'], previsual_path=preview['path'],
                    previsual_sha256=preview['sha256'])
        previews.append({'shot_id': shot['id'], **preview})
    folder = work / 'source_candidates' / digest(candidate)[:24]
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / 'manifest.json'
    if manifest.exists():
        if json.loads(manifest.read_text(encoding="utf-8")) != candidate:
            raise ValueError('Immutable source candidate changed')
    else:
        write_json(manifest, candidate)
    evidence = folder / 'binding.json'
    record = {'parent_manifest_path': spec['manifest_path'],
              'parent_manifest_sha256': spec['manifest_sha256'],
              'manifest_path': str(manifest.resolve()), 'manifest_sha256': sha256(manifest),
              'assets': assets, 'source_previews': previews, 'production_approved': False,
              'exact_manifest_review': 'pending'}
    if evidence.exists() and json.loads(evidence.read_text(encoding="utf-8")) != record:
        raise ValueError('Immutable source binding evidence changed')
    if not evidence.exists():
        write_json(evidence, record)
    return {**item, 'manifest_path': str(manifest.resolve()), 'manifest_sha256': sha256(manifest),
            'source_candidate_evidence_path': str(evidence.resolve()),
            'source_candidate_evidence_sha256': sha256(evidence)}
