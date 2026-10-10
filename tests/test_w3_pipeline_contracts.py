"""W3 pre-extraction contracts: exact reasons, result schemas and ordered effects."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from workflow import backends, first_frame, pipeline_coordinator, repair_delivery, research_inbox, roughcut
from workflow.core.io import sha256, write_json
from tests import test_backend_partitions as backend_fixtures
from tests import test_first_frame_visual_only as frame_fixtures
from tests import test_repair_delivery_writeback as repair_fixtures
from tests import test_research_inbox as research_fixtures
from tests import test_roughcut as roughcut_fixtures


@pytest.mark.parametrize('field,value,reason', [
    ('type', 'assets', 'pool members must be ascend_ssh'),
    ('capability', '', 'pool groups need capability and verified resource_id'),
    ('capability', 'slot0', 'pool aliases overlap; distinct capabilities cannot share a device group'),
    ('logical_device_ids', None, 'shared resource requires explicit verified service/device partition'),
    ('physical_cards', 8, 'service partition must own complete physical cards'),
    ('api_port', 30088, 'pool service partitions overlap or describe inconsistent resources'),
    ('remote_root', '/work/slot0/', 'pool service partitions overlap or describe inconsistent resources'),
    ('ready', False, None),
])
def test_backend_partition_exact_contract(field, value, reason):
    pool = backend_fixtures.PartitionTests().config()
    pool['groups'][1][field] = value
    if reason:
        with pytest.raises(ValueError) as raised:
            backends.members(pool)
        assert str(raised.value) == reason
    else:
        assert backends.members(pool) == pool['groups'][:1]


@pytest.mark.parametrize('area,field,value,reason', [
    ('sources', 'audio_status', 'none', 'invalid audio status'),
    ('sources', 'visual_status', 'unavailable', 'unavailable source cannot supply observations'),
    ('shots', 'source_id', 'missing', 'unknown shot source'),
    ('shots', 'start_seconds', -1, 'overlapping/unordered/out-of-range timecode'),
    ('shots', 'keyframes', [], 'shot needs packaged keyframe evidence'),
    ('templates', 'compatible_templates', ['missing'], 'unknown production template'),
    ('templates', 'shot_ids', ['missing'], 'template needs valid shot evidence'),
    ('topics', 'template_id', 'missing', 'unknown topic template'),
    ('topics', 'theme', 'none', 'invalid theme'),
])
def test_research_bundle_exact_failures(area, field, value, reason):
    bundle = research_fixtures.sample()
    bundle[area][0][field] = value
    with pytest.raises(ValueError) as raised:
        research_inbox.parse_package(research_fixtures.pack(bundle))
    assert str(raised.value) == reason


def test_research_bundle_normalized_snapshot():
    bundle = research_fixtures.sample()
    bundle['topics'][0]['primary_source_urls'] *= 2
    parsed, assets = research_inbox.parse_package(research_fixtures.pack(bundle))
    expected = research_fixtures.sample()
    assert parsed == expected
    assert assets == {'frame.txt': b'fixture'}


@pytest.fixture
def roughcut_config():
    fixture = roughcut_fixtures.RoughcutTests()
    fixture.setUp()
    try:
        yield roughcut.freeze(fixture.config)
    finally:
        fixture.tearDown()


@pytest.mark.parametrize('field,value,reason', [
    ('schema_version', 2, 'roughcut schema_version=1 and kind=editorial_roughcut required'),
    ('duration_seconds', 0, 'roughcut duration must be 1..120 seconds'),
    ('shots', [], 'roughcut needs shots'),
    ('music_gain', 2, 'invalid roughcut music_gain'),
    ('input_sha256', {}, 'freeze all roughcut inputs before rendering'),
])
def test_roughcut_exact_failure_order(roughcut_config, field, value, reason):
    roughcut_config[field] = value
    with patch('workflow.media.duration', side_effect=AssertionError('validation reached media')):
        with pytest.raises(ValueError) as raised:
            roughcut.validate(roughcut_config)
    assert str(raised.value) == reason


def test_roughcut_success_call_order(roughcut_config):
    calls = []
    cues = [{'start': .1, 'end': 1.8}]
    with patch('workflow.media.duration', side_effect=lambda p: calls.append(str(p)) or 2), patch('workflow.caption_timing.events', return_value=cues):
        assert roughcut.validate(roughcut_config) == cues
    assert calls == [roughcut_config['narration_path'], roughcut_config['music_path'], roughcut_config['shots'][0]['source_path']]


@pytest.mark.parametrize('change,reason', [
    ({'worker_sha256': 'changed'}, 'unverified remote worker implementation; cannot interpret failure ordering'),
    ({'raw_sha256': 'changed'}, 'raw video changed during transfer'),
    ({'reference_sha256': []}, 'remote first image differs from the reviewed local reference'),
])
def test_first_frame_evidence_failure_exact(change, reason):
    fixture = frame_fixtures.VisualOnlyReconciliationTests()
    cfg, request, result, evidence = fixture.fixture()
    evidence.update(change)
    with patch('workflow.first_frame.subprocess.run') as run:
        with pytest.raises(ValueError) as raised:
            fixture.verify(cfg, request, result, evidence, Path('/unused.mp4'))
    assert str(raised.value) == reason
    run.assert_not_called()


@pytest.fixture
def repair_candidate():
    fixture = repair_fixtures.RepairDeliveryWriteback()
    fixture.setUp()
    try:
        item, audit = fixture.fixture()
        item.update(state='completed', audit_path=str(audit), new_video_sha256=sha256(audit.with_name('video.mp4')))
        with fixture.q.connect() as connection:
            connection.execute("UPDATE user_repair_requests SET status='completed'")
        yield fixture, item, audit
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize('field,value,reason', [
    ('product_id', 'different', 'full-shot audit belongs to another version'),
    ('receipt', {}, 'full-shot model receipt missing'),
    ('review_pages', [], 'full-shot review pages missing or changed'),
    ('evidence', [], 'missing per-shot audit evidence'),
])
def test_repair_audit_failures_exact(repair_candidate, field, value, reason):
    fixture, item, path = repair_candidate
    audit = json.loads(path.read_text()); audit[field] = value; write_json(path, audit)
    with pytest.raises(repair_delivery.NotDeliverable) as raised:
        repair_delivery.validate_product(fixture.q, item)
    assert str(raised.value) == reason


def test_repair_result_schema_snapshot(repair_candidate):
    fixture, item, audit = repair_candidate
    result = repair_delivery.validate_product(fixture.q, item)
    assert sorted(result) == ['delivery_policy', 'evidence', 'product_id', 'publication_approved', 'request_id', 'sources', 'system_ready', 'title', 'version_job_id', 'video_sha256']
    assert result['system_ready'] is True and result['publication_approved'] is False
    assert result['product_id'] == 'p' and result['version_job_id'] == 'p-new'
    assert result['sources'] == {name: str(audit.with_name(name)) for name in ('video.mp4', 'cover.png', 'publication.txt')}
    assert result['video_sha256'] == item['new_video_sha256']


@pytest.mark.parametrize('state', ['awaiting_images', 'awaiting_singleton_admission'])
def test_coordinator_downstream_order_and_schema(tmp_path, state):
    manifest = tmp_path / 'manifest.json'; write_json(manifest, {'shots': []})
    spec = {'manifest_path': str(manifest), 'narrative_review_path': 'review'}
    calls = []
    result = {'state': state, 'detail': 'unchanged'}
    with patch('workflow.narrative_gate.require_review', side_effect=lambda *a: calls.append('review')), patch('workflow.pipeline_coordinator.infrastructure_ready', side_effect=lambda *a: calls.append('runtime')), patch('workflow.coordinator_sources.prepare_source_candidate', side_effect=lambda item, *a: calls.append('prepare') or item), patch('workflow.storyboard_downstream.run_product', side_effect=lambda *a: calls.append('run') or result):
        actual = pipeline_coordinator.production_step(spec, 'downstream', tmp_path)
    expected = {'state': state, 'detail': result}
    if state == 'awaiting_singleton_admission':
        expected['phase'] = 'delivery'
        assert json.loads((tmp_path / 'registration.json').read_text()) == result
    assert actual == expected
    assert calls == ['review', 'runtime', 'prepare', 'run']


def test_coordinator_content_hold_precedes_runtime(tmp_path):
    manifest = tmp_path / 'manifest.json'; write_json(manifest, {})
    finding = tmp_path / 'finding.json'; write_json(finding, {'blocks_registration': True})
    context = {'path': str(finding)}
    spec = {'manifest_path': str(manifest), 'narrative_review_path': 'review', 'audit_context': [context]}
    with patch('workflow.narrative_gate.require_review'), patch('workflow.pipeline_coordinator.infrastructure_ready') as runtime:
        assert pipeline_coordinator.production_step(spec, 'assets', tmp_path) == {'state': 'isolated_content', 'detail': {'reason': 'Unresolved content finding', 'evidence': context}}
    runtime.assert_not_called()


@pytest.mark.parametrize('case,expected', [
    ('prompt', {'state': 'awaiting_authored_prompts', 'detail': {'shot': 'S1', 'role': 'start'}}),
    ('budget', {'state': 'isolated_budget', 'detail': {'required_images': 1, 'max_images': 0}}),
    ('images', {'state': 'isolated_images', 'detail': {'tasks': [{'id': 'task', 'state': 'failed'}]}}),
    ('ready', {'state': 'pending', 'phase': 'downstream'}),
])
def test_coordinator_asset_gates_snapshot(tmp_path, case, expected):
    manifest = tmp_path / 'manifest.json'
    shot = {'id': 'S1', 'keyframe_prompts': {} if case == 'prompt' else {'start': 'authored'}}
    write_json(manifest, {'shots': [shot]})
    spec = {'manifest_path': str(manifest), 'manifest_sha256': 'bound', 'product_id': 'p', 'narrative_review_path': 'review', 'max_images': 0 if case == 'budget' else 1}
    folder = tmp_path / 'images'; folder.mkdir()
    write_json(folder / 'task.json', {'id': 'task', 'state': 'failed' if case == 'images' else 'completed'})
    calls = []
    with patch('workflow.narrative_gate.require_review'), patch('workflow.pipeline_coordinator.infrastructure_ready'), patch('workflow.plan.validate', side_effect=lambda *a: calls.append('plan')), patch('workflow.coordinator_sources.validate_builders', side_effect=lambda *a: calls.append('builders')), patch('workflow.storyboard_downstream.validate_source_timing', side_effect=lambda *a: calls.append('timing')), patch('workflow.first_frame.reference_roles', return_value=['start']), patch('workflow.storyboard_image_jobs._canvas', return_value={}), patch('workflow.storyboard_image_jobs.ImageJobs') as jobs, patch('workflow.external_image_executor.payload_for', side_effect=lambda *a: calls.append('payload')), patch('workflow.external_image_executor.ExternalImages') as executor, patch('workflow.coordinator_sources.build_sources', side_effect=lambda *a: calls.append('sources')):
        jobs.return_value.folder = folder
        jobs.return_value.add.side_effect = lambda *a: calls.append('add')
        executor.return_value.run.side_effect = lambda **kw: calls.append('run')
        assert pipeline_coordinator.production_step(spec, 'assets', tmp_path) == expected
    expected_calls = ['plan', 'builders', 'timing']
    if case in ('images', 'ready'):
        expected_calls += ['add', 'payload', 'run']
    if case == 'ready':
        expected_calls += ['sources', 'timing']
    assert calls == expected_calls
