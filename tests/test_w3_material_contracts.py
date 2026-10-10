"""W3 anchors recorded on c83b025 before helper extraction; all writes are temporary."""
import copy
import datetime as dt
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from workflow.material_pipeline import core, production_bridge as bridge
from workflow import material_preparation as preparation
from workflow import material_production as production
from workflow import mpt_composition_adapter as mpt


@pytest.fixture
def package(tmp_path):
    body = 'Published October 6, 2026. The team reported a working prototype.'
    for name, data in [('article', body), ('archive', body), ('clip', 'clip'), ('frame', 'frame')]:
        (tmp_path / name).write_text(data)
    event = {'entity': 'Team', 'action': 'reported', 'date': '2026-10-06', 'object': 'prototype'}
    fact = {'id': 'fact', 'subject': 'Team', 'predicate': 'reported', 'object': 'prototype',
            'modality': 'reported', 'text': 'The team reported a working prototype.', 'verification': 'source_supported',
            'evidence': [{'source_id': 'source', 'quote': 'The team', 'start': 27, 'end': 35}]}
    start = body.index('The team'); fact['evidence'][0].update(start=start, end=start + 8)
    fact['fingerprint'] = core.fact_id(fact)
    source = {'id': 'source', 'url': 'https://example.org/article', 'published': '2026-10-06',
              'archive_path': 'archive', 'sha256': core.sha256(tmp_path / 'archive'),
              'text_path': 'article', 'text_sha256': core.sha256(tmp_path / 'article'),
              'date_evidence': 'October 6, 2026'}
    asset = {'id': 'asset', 'url': 'https://example.org/clip', 'path': 'clip', 'sha256': core.sha256(tmp_path / 'clip'),
             'bytes': 4, 'source_id': 'source', 'rights': {'status': 'original', 'evidence_path': 'article'},
             'duration': 5, 'use': 'production'}
    return {'schema_version': 1, 'content_id': core.content_id(event), 'event': event, 'title': 'Prototype',
            'sources': [source], 'facts': [fact], 'assets': [asset],
            'shots': [{'id': 'shot', 'asset_id': 'asset', 'start': 0, 'end': 5,
                       'status': 'visually_reviewed', 'observation': 'visible prototype',
                       'keyframes': [{'at': 1, 'path': 'frame', 'sha256': core.sha256(tmp_path / 'frame')}]}],
            'transcript': {'status': 'not_applicable'}, 'editorial': {'beats': [{'fact_ids': ['fact']}]},
            'collection_pending': [], 'production_pending': [], 'production_asset_plan_reviewed': True}


def test_core_valid_identity_and_hash_call_order(package, tmp_path, monkeypatch):
    calls = []; original = core.sha256
    monkeypatch.setattr(core, 'sha256', lambda p: calls.append(Path(p).name) or original(p))
    assert core.validate(package, tmp_path) is package
    assert calls == ['archive', 'article', 'clip', 'frame']
    assert core.readiness(package, {'status': 'clear_in_scanned_scope'}) == {
        'collection_complete': True, 'research_ready': True, 'production_ready': True,
        'production_approved': False, 'queue_jobs_created': 0, 'blockers': []}


@pytest.mark.parametrize(('section', 'field', 'value', 'error'), [
    (None, 'schema_version', 2, 'schema_version must be 1'),
    (None, 'content_id', 'bad', 'content_id does not match event'),
    (None, 'title', '', 'title, sources and facts required'),
    ('sources', 'sha256', 'bad', 'source hash mismatch'),
    ('sources', 'date_evidence', 'October 7, 2026', 'publication date lacks exact visible evidence'),
    ('facts', 'modality', 'certain', 'explicit fact modality required'),
    ('facts', 'fingerprint', 'bad', 'fact fingerprint mismatch'),
    ('facts', 'evidence', [], 'fact needs source evidence'),
    ('assets', 'sha256', 'bad', 'asset id/hash mismatch'),
    ('assets', 'source_id', 'missing', 'asset source missing'),
    ('assets', 'rights', {'status': 'unlicensed'}, 'invalid rights status'),
    ('assets', 'duration', True, 'invalid asset duration'),
    ('assets', 'duration', float('inf'), 'invalid asset duration'),
    ('shots', 'start', float('nan'), 'duplicate shot/nonfinite time'),
    ('shots', 'end', 6, 'overlapping/out-of-range shot'),
    ('shots', 'status', 'approved', 'invalid shot status'),
    ('shots', 'observation', '', 'reviewed shot requires observation'),
    ('shots', 'keyframes', [], 'shot requires frame evidence'),
])
def test_core_rejection_contract(package, tmp_path, section, field, value, error):
    target = package[section][0] if section else package
    target[field] = value
    with pytest.raises(ValueError) as caught:
        core.validate(package, tmp_path)
    assert str(caught.value) == error


@pytest.mark.parametrize(('section', 'error'), [('sources', 'duplicate source id'), ('facts', 'duplicate fact id'),
                                              ('assets', 'asset id/hash mismatch'), ('shots', 'duplicate shot/nonfinite time')])
def test_core_duplicate_contract(package, tmp_path, section, error):
    package[section].append(copy.deepcopy(package[section][0]))
    with pytest.raises(ValueError) as caught:
        core.validate(package, tmp_path)
    assert str(caught.value) == error


@pytest.mark.parametrize(('case', 'error'), [('span', 'evidence span mismatch'), ('frame', 'invalid frame evidence'),
                                           ('transcript_status', 'invalid transcript status'),
                                           ('transcript_hash', 'transcript hash mismatch'),
                                           ('transcript_time', 'transcript timecode out of range'),
                                           ('editorial', 'editorial references unknown fact')])
def test_core_nested_rejections(package, tmp_path, case, error):
    if case == 'span': package['facts'][0]['evidence'][0]['quote'] = 'wrong'
    if case == 'frame': package['shots'][0]['keyframes'][0]['at'] = 5
    if case == 'editorial': package['editorial']['beats'][0]['fact_ids'] = ['missing']
    if case.startswith('transcript'):
        path = tmp_path / 'transcript.json'; path.write_text(json.dumps({'segments': [{'start': 0, 'end': 6}]}))
        package['transcript'] = {'status': 'reviewed', 'path': path.name, 'sha256': core.sha256(path), 'asset_id': 'asset'}
        if case == 'transcript_status': package['transcript']['status'] = 'approved'
        if case == 'transcript_hash': package['transcript']['sha256'] = 'bad'
    with pytest.raises(ValueError) as caught:
        core.validate(package, tmp_path)
    assert str(caught.value) == error


def test_core_first_failure_precedes_later_sections(package, tmp_path):
    package['sources'][0]['sha256'] = 'bad'; package['facts'] = [None]; package['assets'] = [None]
    with pytest.raises(ValueError, match='^source hash mismatch$'): core.validate(package, tmp_path)


def test_prepare_existing_result_and_interrupted_attempt(tmp_path, monkeypatch):
    request = tmp_path / 'request'; request.write_text('{}')
    monkeypatch.setattr(preparation, 'verified_request', lambda p: {})
    folder = tmp_path / 'worker'; folder.mkdir()
    result = {'state': 'ready', 'unchanged': ['exact', 1]}
    (folder / 'result.json').write_text(json.dumps(result))
    (folder / 'worker_attempt.json').write_text('{}')
    assert preparation.prepare(request, folder) == result
    (folder / 'result.json').unlink()
    with pytest.raises(ValueError) as caught: preparation.prepare(request, folder)
    assert str(caught.value) == 'Interrupted preparation requires receipt reconciliation; no blind retry'


def test_prepare_voice_failure_order_and_receipt(tmp_path, monkeypatch):
    request = tmp_path / 'request'; request.write_text('{}')
    manifest = tmp_path / 'manifest'; manifest.write_text(json.dumps({'narration': [{'text': 'Words'}], 'voice': 'voice'}))
    monkeypatch.setattr(preparation, 'verified_request', lambda p: {'manifest_path': str(manifest), 'content_id': 'source'})
    monkeypatch.setattr('workflow.cloud_speech.synthesize_timed', Mock(side_effect=RuntimeError('voice outcome unknown')))
    monkeypatch.setattr(preparation.time, 'time', lambda: 123)
    monkeypatch.setattr(preparation.os, 'getpid', lambda: 456)
    folder = tmp_path / 'worker'
    with pytest.raises(RuntimeError, match='^voice outcome unknown$'): preparation.prepare(request, folder)
    assert preparation.read(folder / 'worker_status.json') == {
        'state': 'attention', 'phase': 'whole_film_voice', 'error': 'voice outcome unknown',
        'error_type': 'RuntimeError', 'finished': 123, 'automatic_retry': False}
    assert preparation.read(folder / 'worker_attempt.json') == {
        'version': preparation.VERSION, 'request_path': str(request), 'request_sha256': preparation.sha256(request),
        'pid': 456, 'started': 123}
    assert not (folder / 'result.json').exists()


def test_production_disabled_never_requests_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(production, 'require_owner', Mock(side_effect=AssertionError('owner called')))
    assert production.tick(None, tmp_path, None) == []
    assert list(tmp_path.iterdir()) == []


def test_bridge_disabled_schema(tmp_path):
    (tmp_path / 'production_bridge.json').write_text('{"enabled":false}')
    assert bridge.run(tmp_path, tmp_path / 'production') == {'state': 'disabled'}


def test_bridge_run_recovery_and_state_projection(tmp_path, monkeypatch):
    material = tmp_path / 'material'; production_root = tmp_path / 'production'
    (material / 'monitor').mkdir(parents=True); production_root.mkdir()
    config = tmp_path / 'config.json'; config.write_text('{}')
    (material / 'production_bridge.json').write_text(json.dumps({'enabled': True, 'production_root': str(production_root),
        'production_config': str(config), 'planning_config': 'unused'}))
    database = production_root / 'queue.sqlite'; bridge.initialize(database)
    input_path = tmp_path / 'input.json'; input_path.write_text(json.dumps({'package_path': str(config)}))
    with bridge.connection(database) as c:
        for cid, state in [('new', 'awaiting_material_review'), ('lost', 'material_reviewing'), ('ready', 'awaiting_storyboard')]:
            c.execute('INSERT INTO material_intakes VALUES(?,?,?,?,?,?)', (cid, 'hash', str(input_path), state, '{}', 0))
    request = production_root / 'material_intake/ready/production_request.json'; request.parent.mkdir(parents=True); request.write_text('{}')
    work = {'content_id': 'ready', 'state': 'rendering', 'request_sha256': core.sha256(request)}
    bridge.write_json(production_root / 'material_intake/production/state.json', {'items': {'key': work}, 'owner_pid': 12, 'version': 'v'})
    calls = []; monkeypatch.setattr(bridge, 'synchronize', lambda *args: ['intake'])
    monkeypatch.setattr(bridge, 'advance_one', lambda row, *args, **kw: calls.append((row['content_id'], kw)))
    result = bridge.run(material, production_root, reviewer=Mock(), producer=Mock(), planner=Mock())
    assert calls == [('new', {'resume': False})]
    assert result['processed'] == ['new'] and result['received'] == ['intake']
    assert result['state'] == 'connected' and result['video_jobs_created'] == 0 and result['active_material_tasks'] == 0
    rows = {x['content_id']: x for x in result['items']}
    assert rows['lost']['detail'] == {'reason': 'Previous external stage interrupted; inspect its durable attempt and receipts', 'previous_detail': {}}
    assert rows['lost']['state'] == 'material_attention'
    assert rows['ready'] == {'content_id': 'ready', 'state': 'rendering', 'detail': {'main_production': work, 'production_ready': True},
                             'material_review_state': 'awaiting_storyboard'}
    assert json.loads((material / 'production_status.json').read_text()) == result
    assert json.loads((production_root / 'material_intake/status.json').read_text()) == result


@pytest.fixture
def composition(tmp_path, monkeypatch):
    monkeypatch.setattr('workflow.narrative_gate.require_review', lambda *args: None)
    monkeypatch.setattr('workflow.storyboard.enabled', lambda p: True)
    monkeypatch.setattr('workflow.storyboard.missing_previsuals', lambda p: [])
    monkeypatch.setattr('workflow.roughcut.validate', lambda p: p)
    def asset(name, data):
        path = tmp_path / name; path.write_text(json.dumps(data) if not isinstance(data, str) else data)
        return {'path': str(path), 'sha256': mpt.sha(path)}
    source_voice = asset('source.mp3', 'source')
    voice = asset('voice.wav', 'voice'); music = asset('music.wav', 'music')
    preflight = asset('preflight.json', {'shots': [], 'narration': [{'text': 'Words', 'audio_path': source_voice['path']}], 'duration_seconds': 5})
    visual = asset('visual.json', {'status': 'automatic_review_passed', 'receipt': {'model': 'model'}})
    edl = asset('edl.json', {}); preview = asset('preview.mp4', 'preview')
    acceptance = asset('acceptance.json', {'status': 'ready_for_generation', **{key + suffix: item[field]
        for key, item in [('manifest', preflight), ('visual_review', visual), ('edl', edl), ('preview', preview)]
        for suffix, field in [('_path', 'path'), ('_sha256', 'sha256')]}})
    plan = json.loads(Path(preflight['path']).read_text())
    plan.update(id='job', approved_story_preflight=acceptance, input_sha256={source_voice['path']: source_voice['sha256']})
    source = asset('source.json', plan); review = asset('review.json', {})
    results = {'voice': {'audio_path': voice['path'], 'sha256': {voice['path']: voice['sha256']},
                         'segments': [{'text': 'Words', 'source_path': source_voice['path'], 'source_sha256': source_voice['sha256']}]},
               'music': {'audio_path': music['path'], 'sha256': {music['path']: music['sha256']}},
               'compose': {'video_path': str(tmp_path / 'final.mp4')}}
    task_results = asset('results.json', results)
    database = tmp_path / 'queue.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE jobs(id,manifest)'); db.execute('CREATE TABLE tasks(id,status,result)')
        db.execute('INSERT INTO jobs VALUES(?,?)', ('job', json.dumps(plan)))
        db.executemany('INSERT INTO tasks VALUES(?,?,?)', [('job:' + key, 'succeeded', json.dumps(value)) for key, value in results.items()])
    return {'source_manifest': source, 'narrative_review': review, 'acceptance': acceptance, 'task_results': task_results,
            'queue_path': str(database), 'shots': [], 'voice': voice, 'music': music, 'duration_seconds': 5,
            'caption_source': {'path': str(tmp_path / 'captions.ass')}, 'fit_mode': 'contain'}, plan


def test_mpt_production_input_contract(composition):
    manifest, plan = composition
    assert mpt.production_inputs(manifest) == plan


@pytest.mark.parametrize(('field', 'value', 'error'), [('fit_mode', 'cover', 'Preserve complete source framing; no implicit cover crop'),
    ('duration_seconds', 6, 'Duration differs from frozen source'),
    ('shots', [{'id': 'extra'}], 'Complete ordered source shots required'),
    ('voice', {'path': 'wrong'}, 'Audio binding differs from actual result'),
    ('caption_source', {'path': '/wrong'}, 'Caption source must be existing compose artifact')])
def test_mpt_production_binding_rejections(composition, field, value, error):
    manifest, _ = composition; manifest[field] = value
    with pytest.raises(ValueError) as caught: mpt.production_inputs(manifest)
    assert str(caught.value) == error


def test_core_blocker_and_duplicate_reason_order(package):
    duplicate = {'reference': 'old', 'content_id': package['content_id'],
                 'fact_fingerprints': [package['facts'][0]['fingerprint']],
                 'asset_sha256': [package['assets'][0]['sha256']], 'urls': [package['sources'][0]['url']],
                 'fact_text': package['facts'][0]['text']}
    dedup = core.deduplicate(package, [duplicate])
    assert dedup['matches'] == [{'reference': 'old', 'reasons': ['same_event', 'same_canonical_fact',
        'same_media_bytes_requires_editorial_review', 'same_source_requires_editorial_review',
        'fact_text_overlap_requires_editorial_review'], 'fact_overlap': 1.0}]
    package['collection_pending'] = ['source']; package['production_pending'] = ['custom', 'custom']
    package['shots'][0]['status'] = 'candidate'; package['transcript']['status'] = 'pending'
    package['assets'][0]['rights']['status'] = 'unknown'; package['production_asset_plan_reviewed'] = False
    assert core.readiness(package, dedup) == {'collection_complete': False, 'research_ready': False,
        'production_ready': False, 'production_approved': False, 'queue_jobs_created': 0,
        'blockers': ['complete_shot_review', 'custom', 'dedup_editorial_review', 'production_asset_plan_review',
                     'research_evidence_incomplete', 'rights_asset', 'transcript_review']}


def test_verified_request_complete_binding(tmp_path, monkeypatch):
    folder = tmp_path / 'source'; folder.mkdir(); root = tmp_path / 'material'
    package = root / 'inbox/source/package.json'; package.parent.mkdir(parents=True); package.write_text('{}')
    manifest = folder / 'manifest.json'; manifest.write_text('{"source_story_contract":{"exact":"story"}}')
    story = folder / 'material_review.json'; story.write_text('{"review":{"verdict":"pass"},"receipt":{"model":"model"}}')
    request = {'content_id': 'source', 'state': 'awaiting_storyboard', 'material_root': str(root),
               'package_path': str(package), 'package_sha256': preparation.sha256(package),
               'story_review_path': str(story), 'story_review_sha256': preparation.sha256(story)}
    for key in ('manifest', 'narrative_review', 'keyframe_request'):
        path = manifest if key == 'manifest' else folder / (key + '.json')
        if key != 'manifest': path.write_text('{}')
        request.update({key + '_path': str(path), key + '_sha256': preparation.sha256(path)})
    (folder / 'input.json').write_text(json.dumps({key: request[key] for key in ('content_id', 'package_path', 'package_sha256', 'material_root')}))
    path = folder / 'production_request.json'; path.write_text(json.dumps(request))
    calls = []
    monkeypatch.setattr('workflow.material_pipeline.story_gate.require_review', lambda *args: calls.append('story') or {'exact': 'story'})
    monkeypatch.setattr('workflow.narrative_gate.require_review', lambda *args: calls.append('narrative'))
    monkeypatch.setattr(preparation, 'validate', lambda p: calls.append('manifest'))
    assert preparation.verified_request(path) == request
    assert calls == ['story', 'narrative', 'manifest']


@pytest.mark.parametrize(('kind', 'error'), [('state', 'Completed model review receipt required'),
    ('model', 'Completed model review receipt required'), ('facts', None)])
def test_bridge_review_contract(tmp_path, package, monkeypatch, kind, error):
    source = tmp_path / 'package.json'; source.write_text(json.dumps(package))
    packet = tmp_path / 'input.json'; packet.write_text(json.dumps({'package_path': str(source)}))
    folder = tmp_path / 'material_intake' / package['content_id']; folder.mkdir(parents=True)
    source.with_name('receipt.json').write_text('{}')
    row = {'content_id': package['content_id'], 'package_sha256': core.sha256(source), 'input_path': str(packet)}
    monkeypatch.setattr(bridge, 'validate', lambda *args: None)
    monkeypatch.setattr(bridge.story_gate, 'disposition', lambda *args: None)
    monkeypatch.setattr(bridge, 'eligibility', lambda *args: ('awaiting_material_review', 'ready'))
    monkeypatch.setattr(bridge, 'production_topic', lambda *args: ({'facts': [{'id': 'fact'}]}, {}))
    monkeypatch.setattr(bridge.story_gate, 'source_documents', lambda *args: [])
    monkeypatch.setattr(bridge.story_gate, 'require_source_story', lambda *args: None)
    states = []; monkeypatch.setattr(bridge, 'set_state', lambda *args: states.append((args[2], args[3])))
    answer = {'verdict': 'pass', 'issues': [], 'fact_checks': []}
    receipt = {'model': 'model', 'usage': {}}
    if kind == 'state': answer['verdict'] = 'approved'
    if kind == 'model': receipt['model'] = ''
    reviewer = lambda *args, **kw: (answer, receipt)
    if error:
        with pytest.raises(ValueError) as caught: bridge.advance_one(row, tmp_path, tmp_path, {}, reviewer, Mock(), Mock())
        assert str(caught.value) == error
    else:
        assert bridge.advance_one(row, tmp_path, tmp_path, {}, reviewer, Mock(), Mock()) is None
        assert states[-1] == ('material_needs_revision', {'issues': ['Every fact must have its own supported entailment verdict'], 'reason': None})
        record = json.loads((folder / 'material_review.json').read_text())
        assert record['review']['verdict'] == 'revise'
    assert states[0][0] == 'material_reviewing'
