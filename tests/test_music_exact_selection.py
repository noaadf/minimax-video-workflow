"""No cache, recovery or fresh-render path may publish a substituted music window."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from workflow import media
from workflow.core.io import digest, sha256, write_json


def setup_request(tmp_path, monkeypatch, selection, *, pending=False, fresh=False, defaults=False):
    root = tmp_path / 'cache'
    monkeypatch.setenv('WORKFLOW_AUDIO_CACHE', str(root))
    source = tmp_path / 'source.wav'; source.write_bytes(b'source-fixture')
    plan = {'music': {'path': str(source)}, 'narration': [{'end': 28}]}
    if not defaults:
        plan['music']['offset_seconds'] = 145
        plan['duration_seconds'] = 46.162
    identity = digest({'recipe': 4, 'source_sha256': sha256(source), 'music': plan['music'],
                       'duration': plan.get('duration_seconds', 30), 'last_line_end': 28})
    folder = root / 'music' / identity; folder.mkdir(parents=True)
    audio = folder / 'music.wav'; audio.write_bytes(b'old-cached-audio')
    evidence = audio.with_suffix('.selection.json'); write_json(evidence, selection)
    receipt = folder / 'receipt.json'; write_json(receipt, {})
    record = {'schema_version': 2, 'identity': identity, 'sha256': sha256(audio),
              'selection_sha256': sha256(evidence), 'duration_seconds': plan.get('duration_seconds', 30)}
    if not fresh:
        write_json(folder / 'pending_receipt.json' if pending else receipt, record)
    dest = tmp_path / 'result.wav'; dest.write_bytes(b'previous-destination')
    dest.with_suffix('.selection.json').write_bytes(b'previous-evidence')
    return plan, folder, dest


def bytes_in(folder):
    return {path.name: path.read_bytes() for path in folder.iterdir() if path.name != 'write.lock'}


@pytest.mark.parametrize('selection', [
    {'offset_seconds': 1, 'span_seconds': 60},
    {'offset_seconds': 145, 'span_seconds': 30},
    {'offset_seconds': 145}, {'span_seconds': 46.162}, {},
    {'offset_seconds': '145', 'span_seconds': 46.162},
    {'offset_seconds': 145, 'span_seconds': float('nan')},
])
@pytest.mark.parametrize('pending', [False, True])
def test_bound_but_wrong_cached_selection_is_rejected_without_mutation(tmp_path, monkeypatch, selection, pending):
    plan, folder, dest = setup_request(tmp_path, monkeypatch, selection, pending=pending)
    before = bytes_in(folder)
    with patch.object(media, '_render_music') as render, patch.object(media, 'run') as run, \
         pytest.raises(ValueError) as caught:
        media.music(plan, dest)
    assert 'requested offset=145, length=46.162' in str(caught.value)
    assert 'recorded selection' in str(caught.value)
    assert 'Choose an explicit source offset and duration' in str(caught.value)
    assert bytes_in(folder) == before
    assert dest.read_bytes() == b'previous-destination'
    assert dest.with_suffix('.selection.json').read_bytes() == b'previous-evidence'
    render.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize('selection', [
    {'offset_seconds': 1, 'span_seconds': 60},
    {'offset_seconds': 145, 'span_seconds': 30},
    {'offset_seconds': 145}, {},
])
def test_wrong_fresh_selection_cannot_publish_cache_or_destination(tmp_path, monkeypatch, selection):
    plan, folder, dest = setup_request(tmp_path, monkeypatch,
                                      {'offset_seconds': 145, 'span_seconds': 46.162}, fresh=True)
    before = bytes_in(folder)
    def render(plan, path):
        Path(path).write_bytes(b'wrong-new-audio')
        write_json(Path(path).with_suffix('.selection.json'), selection)
    with patch.object(media, '_render_music', side_effect=render), \
         patch.object(media, 'duration', return_value=46.162), \
         pytest.raises(ValueError, match='recorded selection'):
        media.music(plan, dest)
    assert bytes_in(folder) == before
    assert dest.read_bytes() == b'previous-destination'
    assert dest.with_suffix('.selection.json').read_bytes() == b'previous-evidence'


@pytest.mark.parametrize('defaults', [False, True])
@pytest.mark.parametrize('pending', [False, True])
def test_exact_cached_selection_reuses_without_probe_or_render(tmp_path, monkeypatch, defaults, pending):
    selection = {'offset_seconds': 0, 'span_seconds': 30} if defaults else {
        'offset_seconds': 145, 'span_seconds': 46.162}
    plan, folder, dest = setup_request(tmp_path, monkeypatch, selection, pending=pending, defaults=defaults)
    before = bytes_in(folder)
    with patch.object(media, '_render_music') as render, patch.object(media, 'run') as run, \
         patch.object(media, 'duration') as duration:
        media.music(plan, dest)
    assert dest.read_bytes() == b'old-cached-audio'
    result = json.loads(dest.with_suffix('.selection.json').read_text())
    assert result['cache_hit'] is True
    assert (result['offset_seconds'], result['span_seconds']) == tuple(selection.values())
    assert (folder / 'music.wav').read_bytes() == before['music.wav']
    assert (folder / 'music.selection.json').read_bytes() == before['music.selection.json']
    if pending:
        assert not (folder / 'pending_receipt.json').exists()
        assert (folder / 'receipt.json').read_bytes() == before['pending_receipt.json']
    else:
        assert bytes_in(folder) == before
    render.assert_not_called(); run.assert_not_called(); duration.assert_not_called()
