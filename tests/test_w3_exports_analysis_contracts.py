"""Contract snapshots for read-only migration planning and synthetic stage analysis."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import analyze_production_bottlenecks as analysis
from scripts import migrate_exports_to_cards as migration
from workflow.core.io import sha256, write_json


def _video(root, folder, manifest=None, receipt=None):
    path = root / 'exports' / folder
    path.mkdir(parents=True)
    (path / 'video.mp4').write_bytes(folder.encode())
    if manifest is not None:
        write_json(path / 'manifest.json', manifest)
    if receipt is not None:
        write_json(path / 'receipt.json', receipt)
    return path


def test_missing_exports_schema_and_database_order(tmp_path):
    with patch.object(migration, '_database', return_value=({}, [], {})) as database:
        actual = migration.build_plan(tmp_path)
    database.assert_called_once_with(tmp_path)
    assert actual == {'dry_run': True, 'root': str(tmp_path), 'statistics': {'chains': 0, 'orphan_directories': 0, 'version_directories': 0, 'archive_directories': 0, 'versions': 0}, 'chains': [], 'orphans': [], 'archive_moves': [], 'blockers': [], 'untouched_files': []}


def test_export_plan_exact_version_snapshot(tmp_path):
    folder = _video(tmp_path, 'p', {'id': 'p', 'title': 'Title'}, {'exported_at': 10})
    checksum = sha256(folder / 'video.mp4')
    write_json(tmp_path / 'output/production/runs/p/card.json', {'versions': [{'version': 3, 'job_id': 'p', 'video_sha256': checksum, 'acceptance': {'conclusion': 'historical_unverified'}}]})
    write_json(tmp_path / 'output/production/batch/status.json', {'path': 'exports/p/video.mp4'})
    (tmp_path / 'exports' / 'loose.txt').write_text('preserved')
    with patch.object(migration, '_database', return_value=({}, [('tasks', 't1', 'exports/p/video.mp4')], {'p': 'new'})), patch.object(migration.time, 'time', return_value=123):
        actual = migration.build_plan(tmp_path)
    assert actual['statistics'] == {'chains': 1, 'orphan_directories': 0, 'version_directories': 1, 'versions': 1, 'archive_directories': 1, 'blockers': 2, 'flat_deliveries': 0}
    assert actual['chains'] == [{'product_id': 'p', 'versions': [{'source': 'exports/p', 'product_id': 'p', 'job_id': 'p', 'video_name': 'video.mp4', 'video_sha256': checksum, 'title': 'Title', 'suffix_chain': [], 'parent_job_id': None, 'created_at': 10, 'repair_reason': '历史导入（未记录返修原因）', 'authoritative_current_job': 'new', 'acceptance': 'historical_unverified', 'make_current': False, 'deliver': False, 'version': 3, 'target': 'output/production/runs/p/versions/3', 'card': 'output/production/runs/p/card.json'}], 'distinct_versions': 1}]
    assert actual['blockers'] == [{'kind': 'live_path_reference', 'source': 'exports/p', 'table': 'tasks', 'id': 't1'}, {'kind': 'state_path_reference', 'source': 'exports/p', 'path': 'output/production/batch/status.json'}]
    assert actual['untouched_files'] == ['exports/loose.txt']
    assert actual['archive_moves'] == [{'source': 'exports/p', 'target': migration.ARCHIVE + '/p'}]
    assert actual['generated_at'] == 123 and actual['dry_run'] is True
    assert (folder / 'video.mp4').read_bytes() == b'p'


@pytest.mark.parametrize('case,reason', [('unknown', 'No manifest/receipt/known legacy chain'), ('object', 'Expected JSON object: '), ('cycle', 'Cyclic ancestry')])
def test_migration_orphan_reasons(tmp_path, case, reason):
    folder = _video(tmp_path, 'unknown')
    plans = {}
    if case == 'object':
        write_json(folder / 'manifest.json', [])
    if case == 'cycle':
        write_json(folder / 'manifest.json', {'id': 'a'})
        plans = {'a': {'id': 'a', 'parent_job_id': 'b'}, 'b': {'id': 'b', 'parent_job_id': 'a'}}
    with patch.object(migration, '_database', return_value=(plans, [], {})):
        actual = migration.build_plan(tmp_path)
    if case == 'object':
        reason += str(folder / 'manifest.json')
    assert actual['orphans'] == [{'path': 'exports/unknown', 'reason': reason}]
    assert actual['chains'] == []


def test_migration_collision_and_sha_blocker_order(tmp_path):
    _video(tmp_path, 'p', {'id': 'p'}, {'video_sha256': 'changed'})
    (tmp_path / migration.ARCHIVE / 'p').mkdir(parents=True)
    with patch.object(migration, '_database', return_value=({}, [], {})):
        actual = migration.build_plan(tmp_path)
    assert actual['blockers'] == [{'kind': 'archive_collision', 'path': str(tmp_path / migration.ARCHIVE / 'p')}, {'kind': 'receipt_sha_mismatch', 'path': 'exports/p'}]


def _task(stage, *, data=None, result=None, status='succeeded', attempts=1, job='pilot6_demo', deps=()):
    return {'id': job + ':' + stage, 'job_id': job, 'status': status, 'stage': stage, 'result': json.dumps(result or {}), 'payload': json.dumps({'data': data or {}}), 'deps': json.dumps(deps), 'attempts': attempts, 'capability': 'local'}


def test_analysis_cohorts_waiting_and_samples_snapshot(tmp_path):
    batch = tmp_path / 'batch'; batch.mkdir()
    write_json(batch / 'a_conditioned_probe/acceptance.json', {'accepted_for_first': False, 'accepted_for_second': True})
    write_json(batch / 'b_conditioned_probe/acceptance.json', {'technical_passed': True})
    write_json(tmp_path / 'controller_status.json', {'items': [{'item_id': 'p', 'current_job': 'j', 'state': 'watching'}]})
    tasks = [_task('shot', data={'reference_images': ['frame']}, result={'elapsed_seconds': 60, 'metrics': {'elapsed_seconds': 50, 'phase_seconds': {'decode': 10}}}), _task('voice', result={'elapsed_seconds': 3, 'reused_audio': True}, deps=('pilot6_demo:shot',)), _task('quality', result={'elapsed_seconds': 4, 'editorial': {'receipt': {'elapsed_seconds': 2}}}), _task('music', result={'elapsed_seconds': 5}, status='pending'), _task('export', result={'elapsed_seconds': 5}, job='foreign'), _task('compose')]
    events = [{'task_id': 'pilot6_demo', 'at': 10, 'event': 'submitted'}, {'task_id': 'pilot6_demo:shot', 'at': 12, 'event': 'claimed'}, {'task_id': 'pilot6_demo:shot', 'at': 72, 'event': 'succeeded'}, {'task_id': 'pilot6_demo:voice', 'at': 70, 'event': 'claimed'}]
    with patch.object(analysis, 'Queue') as queue, patch.object(analysis.time, 'time', return_value=123):
        queue.return_value.connect.return_value.__enter__.return_value.execute.side_effect = [tasks, events]
        actual = analysis.analyze(tmp_path, batch)
    queue.assert_called_once_with(tmp_path / 'queue.sqlite')
    assert not (tmp_path / 'queue.sqlite').exists()
    assert actual['stage_statistics'] == {'shot_fl2va': {'n': 1, 'min_seconds': 60, 'median_seconds': 60, 'p90_seconds': 60, 'max_seconds': 60}, 'voice_cached': {'n': 1, 'min_seconds': 3, 'median_seconds': 3, 'p90_seconds': 3, 'max_seconds': 3}, 'quality': {'n': 1, 'min_seconds': 4, 'median_seconds': 4, 'p90_seconds': 4, 'max_seconds': 4}}
    assert actual['dependency_ready_to_claim'] == {'shot_fl2va': {'n': 1, 'min_seconds': 2, 'median_seconds': 2, 'p90_seconds': 2, 'max_seconds': 2}, 'voice_cached': {'n': 1, 'min_seconds': 0, 'median_seconds': 0, 'p90_seconds': 0, 'max_seconds': 0}}
    assert actual['samples'] == [{'id': 'pilot6_demo:shot', 'cohort': 'shot_fl2va', 'elapsed_seconds': 60, 'capability': 'local', 'attempts': 1, 'remote_worker_seconds': 50, 'remote_phases': {'decode': 10}}, {'id': 'pilot6_demo:voice', 'cohort': 'voice_cached', 'elapsed_seconds': 3, 'capability': 'local', 'attempts': 1}, {'id': 'pilot6_demo:quality', 'cohort': 'quality', 'elapsed_seconds': 4, 'capability': 'local', 'attempts': 1, 'editorial_api_seconds': 2}]
    assert actual['probe_acceptance'] == [{'probe': 'a_conditioned_probe', 'accepted_for_limited_shot_use': True}]
    assert actual['current_films'] == [{'id': 'p', 'job': 'j', 'state': 'watching'}]
    assert actual['capacity_scenarios'] == [{'physical_cards': 8, 'independent_groups': 2, 'batch_48_attempts_minutes': [24.0, 24.0]}, {'physical_cards': 16, 'independent_groups': 4, 'batch_48_attempts_minutes': [12.0, 12.0]}, {'physical_cards': 24, 'independent_groups': 6, 'batch_48_attempts_minutes': [8.0, 8.0]}]
    assert actual['at'] == 123
    assert actual['recent_cached_exports'] == []
    assert sorted(actual) == ['at', 'capacity_caveats', 'capacity_scenarios', 'current_films', 'dependency_ready_to_claim', 'next_measurements', 'probe_acceptance', 'probe_acceptance_caveat', 'recent_cached_exports', 'samples', 'scope', 'source_db', 'stage_statistics']
