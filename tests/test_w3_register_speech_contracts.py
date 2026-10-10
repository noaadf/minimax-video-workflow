"""Offline script output and side-effect snapshots, frozen before W3 extraction."""
import hashlib
import json
import re
from pathlib import Path

import pytest
from scripts import build_acceptance_register as register
from scripts import prepare_speech_review as speech


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)
    return path


def snapshot(root, summary, page, calls):
    # Temporary absolute paths and their containing-file hashes are per-run data.
    value = json.dumps({'summary': json.loads(summary.read_text()), 'page': page.read_text(), 'calls': calls},
                       sort_keys=True, ensure_ascii=False)
    value = value.replace(str(root), '<ROOT>')
    value = re.sub(r'\b[0-9a-f]{64}\b', '<SHA256>', value)
    value = re.sub(r'(acceptance-|speech-)[0-9a-f]{12}', r'\1<KEY>', value)
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.mark.parametrize('extras', [False, True])
def test_acceptance_register_snapshot(tmp_path, monkeypatch, capsys, extras):
    production = tmp_path / 'output/production'; base = production / 'pilot_six_20261001'
    videos = []; jobs = []; controllers = []
    for name, rule in register.SCOPES.items():
        folder = base / name
        video = put(folder / 'final.mp4', 'video-' + name); source = put(folder / 'source.mp4', 'source-' + name)
        put(folder / 'manifest.json', {'title': name + '<title>', 'shots': [
            {'id': 'S' + str(i), 'duration_seconds': 3.75, 'source_path': str(source)} for i in range(1, 9)]})
        put(folder / 'quality.json', {'editorial': {'review': {'findings': [{'issue': 'retained <finding>'}]}}})
        if rule.get('evidence'): put(base / rule['evidence'], {'evidence': name})
        row = {'id': 'pilot6_' + name + '_test', 'video': str(video)}
        if extras:
            row.update(delivery_policy={'ready_for_delivery': True}, cloud_editorial={'video_sha256': register.sha256(video),
                'material_findings': ['one'], 'resolutions': [{'explanation': 'resolved <point>'}]})
        videos.append(row); jobs.append({'id': row['id'], 'video_url': '/media/' + str(video.relative_to(production))})
        controllers.append({'item_id': name, 'state': 'quarantined', 'detail': {'content_blockers': ['historical <blocker>']}})
    put(base / 'latest_review_bundle.json', {'videos': videos})
    put(production / 'controller_status.json', {'items': controllers})
    put(production / 'review_packets/catalog.json', {'media_paths': ['existing']})
    if extras:
        put(base / 'bridge_a_closing_continuity/delivery.json', {'video_sha256': register.sha256(videos[0]['video'])})
        candidate = base / 'matext_topology_wide_candidate'
        preview = put(candidate / 'preview.mp4', 'preview')
        put(candidate / 'probe_plan.json', {'target_parent_sha256': register.sha256(videos[-1]['video'])})
        put(candidate / 'preview_receipt.json', {'video': str(preview), 'sha256': register.sha256(preview)})
        integrated = put(base / 'matext_topology_integrated_candidate/final.mp4', 'integrated')
        put(integrated.parent / 'receipt.json', {'parent_sha256': register.sha256(videos[-1]['video']),
            'video': str(integrated), 'video_sha256': register.sha256(integrated)})
    calls = []; original = register.write_json
    monkeypatch.setattr(register, 'ROOT', tmp_path); monkeypatch.setattr(register, 'snapshot', lambda p: {'jobs': jobs})
    monkeypatch.setattr(register.time, 'time', lambda: 123)
    monkeypatch.setattr(register, 'write_json', lambda path, value: calls.append(str(path.relative_to(tmp_path))) or original(path, value))
    assert register.main() is None
    result = json.loads(capsys.readouterr().out); summary = Path(result['summary'])
    report = json.loads(summary.read_text())
    assert result['films'] == 6 and result['pending_groups'] == 10
    assert report['content_approved'] is False and report['generation_submissions'] == 0
    assert report['controller_modified'] is False
    assert calls[-2:] == ['output/production/review_packets/catalog.json', 'output/production/pilot_six_20261001/acceptance_register_index.json']
    actual = snapshot(tmp_path, summary, summary.with_name('index.html'), calls)
    assert actual == {False: '7cdfe286905188ade96a7a4de87022d8db25cea1c983101550d2027df6d1e611', True: 'f789a76bd944d977cc2cd0b4cb7284b1341d42b21594ab0ba8dd117f55bec3b0'}[extras], actual


@pytest.mark.parametrize('diagnosed', [False, True])
def test_speech_review_snapshot_and_ff_order(tmp_path, monkeypatch, capsys, diagnosed):
    base = tmp_path / 'output/production/pilot_six_20261001'; production = base.parent
    code = put(tmp_path / 'workflow/speech_qc.py', 'frozen comparison code')
    mixes = []; stems = []; inputs = []
    for name, recognized in [('suspect', 'changed'), ('matching', 'same')]:
        video = put(base / name / 'video.mp4', 'video-' + name); voice = put(video.parent / 'voice.wav', 'voice-' + name)
        manifest = put(video.parent / 'manifest.json', {'title': name + '<title>', 'narration': [
            {'text': text, 'start': start, 'end': start + 2} for text, start in [('AB', 1), ('CD', 10), ('EF', 20)]]})
        report = put(video.parent / 'report.json', {})
        mixes.append({'id': name + '-id', 'name': name, 'expected': 'ABCDEF', 'recognized': recognized,
                      'video': str(video), 'report_path': str(report)})
        if name == 'suspect': stems.append({'name': name, 'expected': 'ABCDEF', 'recognized': 'stem', 'report_path': str(report)})
        inputs.append({'name': name, 'assets': {key: {'path': str(path), 'sha256': speech.sha256(path)}
                                              for key, path in [('video', video), ('voice', voice), ('manifest', manifest)]}})
    put(base / 'speech_audit_large/index.json', {'items': mixes})
    put(base / 'speech_stem_review/index.json', {'items': stems})
    put(base / 'temporal_audit_input.json', {'items': inputs})
    put(production / 'review_packets/catalog.json', {'preserved': True})
    if diagnosed:
        raw = put(base / 'raw.mp3', 'raw')
        put(base / 'speech_source_diagnosis.json', {'items': [{'name': 'suspect', 'line': 2, 'text': 'EF',
            'video_sha256': inputs[0]['assets']['video']['sha256'], 'voice_sha256': inputs[0]['assets']['voice']['sha256'],
            'raw_source_path': str(raw), 'source_sha256': speech.sha256(raw), 'raw_source_url': '/media/raw.mp3',
            'waveform_correspondence': True, 'duration_fits_without_truncation': False, 'tempo': 1.25}]})
    differences = [{'classification': 'phonetic_difference', 'expected_range': [i, i + 1],
                    'expected_written': 'A<&', 'recognized_written': 'X'} for i in (0, 2, 4)]
    def classify(expected, recognized):
        return {'differences': differences if recognized == 'changed' else differences[-1:] if recognized == 'stem' else []}
    calls = []; original = speech.write_json
    monkeypatch.setattr(speech, 'ROOT', tmp_path); monkeypatch.setattr(speech.time, 'time', lambda: 123)
    monkeypatch.setattr(speech, 'normalize', lambda text: text); monkeypatch.setattr(speech, 'classify_phonetics', classify)
    def ff(*args):
        calls.append(['ff', *map(str, args)]); Path(args[-1]).write_bytes(b'bounded clip')
    monkeypatch.setattr(speech.media, 'ff', ff)
    monkeypatch.setattr(speech, 'write_json', lambda path, value: calls.append(['write', str(path)]) or original(path, value))
    assert speech.main() is None
    result = json.loads(capsys.readouterr().out); summary = Path(result['summary'])
    report = json.loads(summary.read_text())
    assert result['packet_count'] == 2 and result['films_routed'] == 1
    assert [p['line'] for p in report['items'][0]['packets']] == [2, 0]
    assert [call[1] for call in calls[:4]] == ['-ss'] * 4
    assert [call[-1].split('/')[-1] for call in calls[:4]] == ['phrase_1.mp4', 'phrase_1_voice.mp3', 'phrase_2.mp4', 'phrase_2_voice.mp3']
    assert report['items'][1]['routing'] == 'homophone_spelling_only_or_match'
    actual = snapshot(tmp_path, summary, summary.with_name('index.html'), calls)
    assert actual == {False: 'f7a0b9d1c7ad9e46823de43ebe9ba9a02337b97c26f2c775e8d5ba0cb786c3e4', True: 'e8a4c24df0075452cf962b3b771738ecda57b26af82ecf9e8d14f711e248167c'}[diagnosed], actual


def test_speech_packet_replay_keeps_existing_bytes(tmp_path, monkeypatch):
    # The end-to-end fixtures above freeze first-write behavior; replay must not
    # invoke a media process when both packet artifacts already exist.
    out = tmp_path / 'out'; folder = out / 'film'; folder.mkdir(parents=True)
    for filename in ('phrase_1.mp4', 'phrase_1_voice.mp3'):
        (folder / filename).write_bytes(b'existing')
    monkeypatch.setattr(speech.media, 'ff', lambda *args: pytest.fail('Existing packet was regenerated'))
    chosen = [{'line': 0, 'start': 1, 'end': 2, 'expected': 'AB', 'differences': [], 'also_flagged_in_stem': False}]
    paths = []
    packets = speech._comparison_packets(chosen, out, {'name': 'film', 'video': 'unused'},
        {'assets': {'voice': {'path': 'unused'}}}, tmp_path, {}, paths)
    assert len(packets) == 1 and len(paths) == 2
    assert all(Path(path).read_bytes() == b'existing' for path in paths)
