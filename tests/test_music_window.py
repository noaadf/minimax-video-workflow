
import pytest

from workflow.core.io import sha256, write_json
from workflow.music_window import select


def cached_source(tmp_path, monkeypatch, spans):
    monkeypatch.setenv('WORKFLOW_AUDIO_CACHE', str(tmp_path))
    source = tmp_path / 'source.wav'; source.write_bytes(b'fixture')
    write_json(tmp_path / 'music_sources' / ('v2_' + sha256(source) + '.json'), {'spans': spans})
    return source


@pytest.mark.parametrize('offset,length', [(145, 46.162), (190, 5), (145, 100), (0, 2)])
def test_no_offset_substitution_or_length_shortening(tmp_path, monkeypatch, offset, length):
    spans = [[1.0, 100.0], [140.0, 180.0]]
    source = cached_source(tmp_path, monkeypatch, spans)
    with pytest.raises(ValueError) as caught:
        select(source, offset, length)
    message = str(caught.value)
    assert f'offset={offset}' in message
    assert f'length={length}' in message
    assert str(spans) in message
    assert 'Choose an explicit offset and length' in message


def test_exact_request_and_span_boundary_are_preserved(tmp_path, monkeypatch):
    source = cached_source(tmp_path, monkeypatch, [[1, 100], [140, 200]])
    assert select(source, 145, 46.162) == (145, 46.162)
    assert select(source, 140, 60) == (140, 60)


@pytest.mark.parametrize('spans,offset,length', [([], 0, 10), ([[0, 10]], -1, 2),
    ([[0, 10]], 0, 0), ([[0, 10]], 0, -1), ([[0, 10]], float('nan'), 2)])
def test_impossible_windows_always_explain_request_and_spans(tmp_path, monkeypatch, spans, offset, length):
    source = cached_source(tmp_path, monkeypatch, spans)
    with pytest.raises(ValueError, match='available spans='):
        select(source, offset, length)
