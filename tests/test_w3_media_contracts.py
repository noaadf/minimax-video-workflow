"""Pin music publication ordering and evidence bytes before W3 extraction."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from workflow import media
from workflow.core.io import digest, sha256, write_json


@pytest.mark.parametrize('mode', ['fresh', 'cached', 'recover'])
def test_music_publication_trace_and_evidence(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('WORKFLOW_AUDIO_CACHE', str(tmp_path / 'cache'))
    source = tmp_path / 'source.wav'
    source.write_bytes(b'source')
    plan = {'music': {'path': str(source)}, 'narration': [{'end': 28}], 'duration_seconds': 30}
    identity = digest({'recipe': 4, 'source_sha256': sha256(source), 'music': plan['music'],
                       'duration': 30, 'last_line_end': 28})
    folder = tmp_path / 'cache' / 'music' / identity
    folder.mkdir(parents=True)
    selection = {'offset_seconds': 0, 'span_seconds': 30}
    audio = folder / 'music.wav'
    receipt = folder / 'receipt.json'
    pending = folder / 'pending_receipt.json'
    if mode != 'fresh':
        audio.write_bytes(b'accepted')
        write_json(audio.with_suffix('.selection.json'), selection)
        write_json(pending if mode == 'recover' else receipt,
                   {'schema_version': 2, 'identity': identity, 'sha256': sha256(audio),
                    'selection_sha256': sha256(audio.with_suffix('.selection.json')), 'duration_seconds': 30})
    events = []
    replace = media.os.replace
    def record_replace(src, dst):
        events.append(('replace', Path(dst).name))
        return replace(src, dst)
    def render(plan, path):
        events.append(('render',))
        Path(path).write_bytes(b'accepted')
        write_json(Path(path).with_suffix('.selection.json'), selection)
    dest = tmp_path / 'result.wav'
    with patch.object(media, '_render_music', side_effect=render) as renderer, \
         patch.object(media, 'duration', side_effect=lambda p: events.append(('duration',)) or 30), \
         patch.object(media.os, 'replace', side_effect=record_replace), \
         patch('uuid.uuid4', return_value=type('UUID', (), {'hex': 'fixed'})()):
        assert media.music(plan, dest) is None
    prefixes = {
        'fresh': [('render',), ('replace', 'fixed.selection.json'), ('duration',),
                  ('replace', 'pending_receipt.json'), ('replace', 'music.wav'),
                  ('replace', 'music.selection.json'), ('replace', 'receipt.json')],
        'cached': [], 'recover': [('replace', 'receipt.json')],
    }
    assert events == prefixes[mode] + [('replace', 'result.wav.fixed.part.selection.json'),
                                     ('replace', 'result.wav'), ('replace', 'result.selection.json')]
    assert renderer.call_count == int(mode == 'fresh')
    assert dest.read_bytes() == b'accepted'
    assert json.loads(dest.with_suffix('.selection.json').read_text()) == {
        **selection, 'cache_hit': mode != 'fresh', 'cache_identity': identity, 'audio_sha256': sha256(audio)}
    assert not pending.exists()
    assert not list(tmp_path.glob('*.part*'))


@pytest.mark.parametrize('duration', [0, -1, float('nan'), 29, 31])
def test_music_wrong_render_duration_preserves_destination(tmp_path, monkeypatch, duration):
    monkeypatch.setenv('WORKFLOW_AUDIO_CACHE', str(tmp_path / 'cache'))
    source = tmp_path / 'source.wav'; source.write_bytes(b'source')
    dest = tmp_path / 'result.wav'; dest.write_bytes(b'previous')
    plan = {'music': {'path': str(source)}, 'narration': [{'end': 28}]}
    def render(plan, path):
        Path(path).write_bytes(b'bad')
    with patch.object(media, '_render_music', side_effect=render), \
         patch.object(media, 'duration', return_value=duration), \
         pytest.raises(ValueError, match='^rendered music has wrong duration$'):
        media.music(plan, dest)
    assert dest.read_bytes() == b'previous'
    assert [p.name for p in (tmp_path / 'cache' / 'music').rglob('*') if p.is_file()] == ['write.lock']
