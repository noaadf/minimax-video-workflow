"""Frozen W3 script orchestration contracts; all external media/process/socket calls faked."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import types
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def normalized(value, root):
    return json.loads(json.dumps(value, default=str).replace(str(root), '<ROOT>'))


REPLAY_SNAPSHOTS = {'empty': '644bc4b46f36f32000c3e743409a62f3595bedba77f275ed0f542bcef9f1239f', 'active': '3506a4cc6c1b2d9bcb05a368958d6abb91bc2a4c280b52b76463399fcfdd7ed0', 'not_allowlisted': 'cadb24705d07d032e55d1a96a781942a179571985caa7c9df52ec5ccc557ff05', 'wrong_parent_sha': '01adc528fc43a7c8b622a466e23531114cd21ce48cd7fe67bd9c6200fec98534', 'bad_frames': 'e8834c6c5cf0ff4d37ff0393d07085b16121c40e78b8ae821e18d1f9465ff8c8', 'partial_retime': 'd742b131232480a5128dc854ba16a4bf1f60579c8a53f0961e044f30adfef684', 'copy': '35bc4a9d9de7d0a8da718c15d2fdde2eff30a93c0bdb9f4b14024d3ec68ed5bd', 'resample': '8ca7fb8e05c2c88b45e15d884f65918ad3c62bfeb45ad2a1ce0a300e6012e0f3', 'replay': 'e2dbef2ecbfd3dc89687f769b34bf782f05fd0c160d1e2a5a2092a3b5fb5b7dc', 'partial_render': '433173e363f647d240af738b6cc863ede4e9e1dd797f9025bf84dfcb39b8fce9', 'captions_changed': 'e18f1a0b29fa257c66141f27c22ac6aee32907a1ac207dd32597ff81aafa0633', 'qc_failed': '6c8fb61508649134f70889e9134dd7f0d9b6458e191e17bdb4b1ea7031e729bc', 'temporal_failed': 'f7b905aff56cd0344a6dfe28f31b663409c12a28c1debe7943f36785777a5a65'}


@pytest.mark.parametrize('mode', list(REPLAY_SNAPSHOTS))
def test_illustration_replay_exact_io_and_failures(tmp_path, mode):
    module = load_script('scripts/fix_illustration_replays.py', 'w3_replay_script')
    module.ROOT = tmp_path
    base = tmp_path / 'output/production/pilot_six_20261001'
    names = ['matext_care_conditioned_probe', 'matext_reading_conditioned_probe',
             'matext_foldpose_conditioned_probe', 'matext_openhero_locked_render']
    for name in names:
        video = base / name / 'video.mp4'
        video.parent.mkdir(parents=True)
        video.write_bytes(name.encode())
        save(video.parent / 'result.json', {'video_path': str(video)})
    source = base / names[0] / 'video.mp4'
    if mode == 'not_allowlisted':
        source = base / 'other.mp4'
        source.write_bytes(b'other')
    parent = base / 'parent/video.mp4'
    parent.parent.mkdir()
    parent.write_bytes(b'parent')
    (parent.parent / 'cover.png').write_bytes(b'cover')
    film = {'id': 'film_matext_a', 'video': str(parent)}
    plan = {'shots': [{'id': sid, 'source_path': str(source)} for sid in ('S1', 'S2')]}
    save(parent.parent / 'manifest.json', plan)
    save(base / 'latest_review_bundle.json', {'videos': [film]})
    count = 96 if mode == 'resample' else 48
    key = module.sha256(source)
    timeline = [{'shot': sid, 'source_sha256': key, 'start_frame': 0,
                 'frames': count, 'global_start_frame': index * count}
                for index, sid in enumerate(('S1', 'S2'))]
    item = {'id': film['id'], 'video_sha256': module.sha256(parent),
            'adjacent_replays': module.adjacent_replays(timeline), 'timeline': timeline}
    if mode == 'wrong_parent_sha':
        item['video_sha256'] = 'wrong'
    if mode == 'empty':
        item['adjacent_replays'] = []
    audit_path = base / 'source_range_audit/report.json'
    save(audit_path, {'items': [item]})
    save(base / 'source_range_audit/index.json', {'report': str(audit_path)})
    save(base / 'temporal_audit_input.json', {'items': [{'name': 'matext_a',
        'intentional_cuts': [], 'assets': {'voice': {'path': 'voice.wav'}}}]})
    out = base / 'illustration_continuity_fix'
    if mode == 'partial_retime':
        (out / 'runs' / f'{key[:12]}-{count * 2}').mkdir(parents=True)
    if mode == 'partial_render':
        (out / ('matext_a-' + 'd' * 12)).mkdir(parents=True)
    trace = []
    snapshots = []
    real_write = module.write_json
    class Queue:
        def __init__(self, path):
            trace.append(['queue', str(path)])
            self.path = str(path)
        def status(self):
            trace.append(['status'])
            return [{'status': 'running'}] if mode == 'active' else []
    def write(path, data):
        trace.append(['write', str(path)])
        snapshots.append([str(path), data])
        real_write(path, data)
    def probe(path):
        trace.append(['probe', str(path)])
        n = 95 if mode == 'bad_frames' else 96 if path == source else count * 2
        return {'streams': [{'codec_type': 'video', 'nb_frames': str(n), 'avg_frame_rate': '24/1'}]}
    def ff(*args, **kwargs):
        trace.append(['ff', args, kwargs])
        target = Path(args[-1])
        if 'cwd' in kwargs:
            target = Path(kwargs['cwd']) / target
        target.write_bytes(b'final' if 'cwd' in kwargs else b'resampled')
    def captions(plan, path):
        trace.append(['captions', str(path)])
        path.write_bytes(b'changed' if mode == 'captions_changed' and path.name == 'captions.ass' else b'captions')
    def render(spec, folder):
        trace.append(['render', spec, str(folder)])
        folder.mkdir(parents=True, exist_ok=True)
        outputs = {}
        for name in spec['outputs']:
            path = folder / (name + '.mp4')
            path.write_bytes(name.encode())
            outputs[name] = {'path': str(path)}
        return {'outputs': outputs}
    def qc(*args):
        trace.append(['qc', args])
        return {'hard_failed': mode == 'qc_failed'}
    def scan(*args, **kwargs):
        trace.append(['scan', args, kwargs])
        return {'technical_scan_passed': mode != 'temporal_failed', 'frames_scanned': 720, 'exceptions': []}
    def run(*args):
        trace.append(['run', args])
        return b'SHA256=audio'
    output = io.StringIO()
    with contextlib.ExitStack() as stack:
        for target, replacement in [
            (('Queue',), Queue), (('write_json',), write), (('digest',), lambda value: 'd' * 64),
            (('render',), render), (('screen_video',), qc), (('scan',), scan),
            (('media', 'probe'), probe), (('media', 'ff'), ff), (('media', 'captions'), captions),
            (('media', 'run'), run), (('media', 'video_duration_matches'), lambda *args: True),
            (('media', 'review_contact'), lambda *args: trace.append(['contact', args])),
            (('fcntl', 'flock'), lambda fd, flags: trace.append(['lock', flags])),
            (('time', 'monotonic'), lambda: 100), (('time', 'time'), lambda: 200)]:
            obj = module
            for attr in target[:-1]:
                obj = getattr(obj, attr)
            stack.enter_context(patch.object(obj, target[-1], replacement))
        stack.enter_context(contextlib.redirect_stdout(output))
        error = None
        try:
            module.main()
            if mode == 'replay':
                module.main()
        except (AssertionError, ValueError, RuntimeError) as exc:
            error = [type(exc).__name__, str(exc)]
    snapshot = normalized({'trace': trace, 'writes': snapshots, 'stdout': output.getvalue(), 'error': error}, tmp_path)
    assert hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest() == REPLAY_SNAPSHOTS[mode]


RANGE_SNAPSHOTS = {False: '9b653382cbd3e52055a9195caf1e7f2f5c24aad32592e07abf40519ff135bd32', True: 'd42dbc90407a0e94fee3e9b59800d60e7a4aad6a990a8179ca9b9d31f1b7ba54'}


@pytest.mark.parametrize('fail', [False, True])
def test_ranges_all_scenarios_order_outputs_and_shutdown_without_socket(tmp_path, fail):
    module = load_script('scripts/ascend_worker_agents/download/test_ranges_v2.py', 'w3_ranges_script')
    module.EVIDENCE = tmp_path / 'evidence.json'
    module.CHUNK = 4
    module.FILES = [('README.md', 8), ('configuration.json', 8), (module.INDEX_JSON, 8),
                    ('FL2VA/one_chunk.safetensors', 4), ('FL2VA/tail.safetensors', 28),
                    ('FL2VA/multi.safetensors', 40)]
    trace = []
    def build():
        trace.append(['build'])
        module.Origin.data = {path: bytes([index + 1]) * size for index, (path, size) in enumerate(module.FILES)}
    class Server:
        server_address = ('127.0.0.1', 1234)
        def __init__(self, address, handler):
            trace.append(['server', address, handler.__name__])
        def serve_forever(self):
            trace.append(['serve'])
        def shutdown(self):
            trace.append(['shutdown'])
    class Thread:
        def __init__(self, target, daemon):
            self.target = target
            trace.append(['thread', daemon])
        def start(self):
            trace.append(['start'])
            self.target()
    @contextlib.contextmanager
    def tempdir(prefix):
        path = tmp_path / prefix
        path.mkdir()
        trace.append(['temp_enter', prefix])
        yield str(path)
        trace.append(['temp_exit', prefix])
    def command_info(command):
        models = Path(command[2])
        return models.parent.name.split('-')[1], models
    def download(models):
        for path, _ in module.FILES:
            target = models / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(module.Origin.data[path])
        total = sum(len(value) for value in module.Origin.data.values())
        save(models / 'download_verification.json', {'complete': True, 'files': len(module.FILES), 'bytes': total})
        save(models / 'download_progress.json', {key: total for key in (
            'done_bytes', 'total_bytes', 'pct', 'remaining_bytes', 'rate_MBps_window', 'rate_MBps_run', 'eta_seconds')})
    def run(command, **kwargs):
        if command[0] == 'curl':
            trace.append(['curl', command, kwargs])
            return subprocess.CompletedProcess(command, 0, 'curl fixture\n', '')
        case, models = command_info(command)
        trace.append(['run', case, normalized(command, tmp_path), kwargs, module.THROTTLE, list(module.FAULTS)])
        if case == 'J':
            return subprocess.CompletedProcess(command, 1, 'another downloader holds the lock', '')
        download(models)
        messages = {'C': 'Content-Range bytes chunk_retry', 'D': 'HTTP 200 for Range request',
                    'F': 'SHA256 mismatch, wiped', 'H': 'stale journal', 'I': 'curl exit 18 chunk_retry'}
        if case == 'B':
            module.SERVED.append(('FL2VA/tail.safetensors', 12, 27, 'ok'))
        if case == 'K':
            module.SERVED.append((module.INDEX_JSON, 0, 7, '200-full'))
        if case == 'I':
            module.SERVED.append(('README.md', 0, 7, 'reset'))
        module.FAULTS.clear()
        return subprocess.CompletedProcess(command, 1 if fail and case == 'D' else 0, messages.get(case, ''), '')
    class Process:
        def __init__(self, command, **kwargs):
            self.case, models = command_info(command)
            trace.append(['popen', self.case, normalized(command, tmp_path), kwargs, module.THROTTLE])
            if self.case == 'E':
                save(models / 'download_progress.json', {'done_bytes': 16})
                save(models / 'one.ranges.json', {'size': 4, 'sha256': 'x', 'prefix': 0, 'completed': []})
        def poll(self):
            trace.append(['poll', self.case])
            return None
        def send_signal(self, sig):
            trace.append(['signal', self.case, int(sig)])
        def wait(self):
            trace.append(['wait', self.case])
        def terminate(self):
            trace.append(['terminate', self.case])
    output = io.StringIO()
    with patch.object(module.Origin, 'build', build), patch.object(module, 'ThreadingHTTPServer', Server), \
         patch.object(module.threading, 'Thread', Thread), patch.object(module.tempfile, 'TemporaryDirectory', tempdir), \
         patch.object(module.subprocess, 'run', run), patch.object(module.subprocess, 'Popen', Process), \
         patch.object(module.time, 'monotonic', return_value=100), \
         patch.object(module.time, 'sleep', side_effect=lambda amount: trace.append(['sleep', amount])), \
         contextlib.redirect_stdout(output):
        result = module.main()
    evidence = json.loads(module.EVIDENCE.read_text())
    snapshot = normalized({'trace': trace, 'evidence': evidence, 'result': result, 'stdout': output.getvalue()}, tmp_path)
    # Interpreter and checkout locations are environment metadata, not the orchestration contract.
    snapshot['evidence']['python'] = '<PYTHON>'
    snapshot = json.loads(json.dumps(snapshot).replace(str(module.V2), '<SCRIPT>').replace(module.sys.executable, '<PYTHON>'))
    assert hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest() == RANGE_SNAPSHOTS[fail]
