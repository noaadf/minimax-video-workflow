"""Frozen driver loop contracts; all queue, worker, process and service effects are fakes."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_deadline30 as deadline
from scripts import run_user_repair30 as repair


class StopLoop(Exception):
    pass


class FakeConnection:
    def __init__(self, calls, request=None, known=()):
        self.calls, self.request, self.known = calls, request, known
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, sql, args=()):
        self.calls.append(('sql', sql, args))
        self.rowcount = 1
        return self
    def fetchone(self):
        return self.request
    def __iter__(self):
        return iter({'id': job} for job in self.known)


def install_fakes(module, tmp_path, monkeypatch, rows=(), known=(), request=None):
    calls = []
    for name in ('LOCK_FD', 'OVERRIDES'):
        if hasattr(module, name): monkeypatch.setattr(module, name, getattr(module, name))
    out = tmp_path / 'output'; out.mkdir()
    q = SimpleNamespace(path=str(tmp_path / 'fake.sqlite'),
        status=lambda: copy.deepcopy(rows),
        connect=lambda: FakeConnection(calls, request, known))
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'OUT', out)
    monkeypatch.setattr(module, 'PYTHON', 'fake-python')
    monkeypatch.setattr(module, 'Queue', lambda path: calls.append(('queue', str(path))) or q)
    monkeypatch.setattr(module.fcntl, 'flock', lambda lock, operation: calls.append(('lock', operation)))
    monkeypatch.setattr(module.os, 'getpid', lambda: 99)
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    def stop(seconds):
        calls.append(('sleep', seconds)); raise StopLoop()
    monkeypatch.setattr(module.time, 'sleep', stop)
    def write(path, value):
        calls.append(('write', str(path), copy.deepcopy(value)))
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    monkeypatch.setattr(module, 'write_json', write)
    def launch(args, *pos, **kwargs):
        # Descriptor numbers are incidental; their propagation is asserted separately.
        if 'lockfd' in kwargs: assert isinstance(kwargs['lockfd'], int)
        calls.append(('launch', args, [str(x) for x in pos if not isinstance(x, int)], sorted(kwargs)))
        return SimpleNamespace(pid=77, returncode=None, poll=lambda: None)
    monkeypatch.setattr(module, 'launch', launch)
    return out, calls


def trace_hash(calls, tmp_path):
    text = json.dumps(calls, sort_keys=True, ensure_ascii=False).replace(str(tmp_path), '<root>')
    return hashlib.sha256(text.encode()).hexdigest()


REPAIR_GOLDENS = {'empty': 'c47100ef84c80a0f2b897d0a00425841442e6b19961d25a58ad846d18f8228ae', 'paused': '86b306855b4701c6dc7ebe7f78de5ded163b591df6f5222a66513d51c2b301d0', 'prepare': '719ef6bae5f285c5041ae9d467880dc9e72aa838d07bd02ed43ec502744cec7f', 'prepared': 'c370dff7017efb6ad95b558922dff5335c36fc8ca40cca649528ce5e9842480b', 'bound_changed': 'faf67d16957bbf51110af31ea5a33f4507401c48b8e2b01752669935a2b33905', 'recovery': 'ede08c6ce13998770b74f0e5007b1bcfdbae19506479433a8fb622a8119b0189', 'render_failed': 'd4fc6d238ccee1e415f49189b899d4a167e0ebd3c0a60a64c6a1766694ff1231', 'audit_success': '6c53aeae8e93cf92bed5daa196f81b45be89163cb414038ecebd8f2aeb89fe8c', 'admission_errors': 'b860e654d7f98855d9e462180ebff2ce73e24e96c842b15d4141bc72d070ad83', 'finished': '6053205a003178add72dc1f71f3d919fbea291ecc14582838479fb52e94e1bd2'}
DEADLINE_GOLDENS = {'empty': 'e510e49c2a71c91baf8028803a077a2386ac953c49be9413e46136f2ae4f6aca', 'paused': '4c5f4963fb6363ad546925bce62fae75643f1b7e0bff0d29496c127dd55248cc', 'recovery': '0b065a661194d9c200888182737c113edb9f075118102d701a8fe810998ff0c1', 'admit_error': 'b330df3f9444de5f0b49b28857862f066a1127f12e2aad6e05f1cdc19cf556f8', 'new_topic': 'f50a386d03796beb122b0586dd07ef66b5ea7b9c8fee8b94f624cab6c502b22f', 'complete': '6e538aeaab0faa1dc39c0c9c9c18829f551b4ab87c1df1e14952914a14acb2d7'}


def repair_trace(mode, tmp_path, monkeypatch):
    item = {'request_id': 'request', 'product_id': 'product', 'job_id': 'old',
            'manifest_path': str(tmp_path / 'manifest.json')}
    request = {'job_id': 'old', 'video_path': 'bound-video', 'video_sha256': 'sha'}
    rows = [{'job_id': 'new', 'status': 'failed' if mode == 'render_failed' else 'succeeded'}]
    out, calls = install_fakes(repair, tmp_path, monkeypatch, rows=rows, request=request)
    overrides = {'product': {'sha256': 'approved'}} if mode in ('prepare', 'prepared', 'bound_changed') else {}
    monkeypatch.setattr(repair, 'narrative_overrides', lambda: copy.deepcopy(overrides))
    monkeypatch.setattr(repair.subprocess, 'run', lambda *args, **kw: calls.append(('preflight', args, kw)))
    monkeypatch.setattr(repair, 'paused', lambda root: mode == 'paused')
    monkeypatch.setattr(repair, 'sha256', lambda path: 'sha')
    monkeypatch.setattr(repair, 'begin_immediate', lambda c: calls.append(('begin_immediate',)))
    monkeypatch.setattr(repair, 'submit', lambda *args: calls.append(('submit', str(args[1]))) or {'job_id': 'new'})
    from workflow import narrative_gate, storyboard_deployment, repair_rollout_bridge, material_production, repair_delivery
    monkeypatch.setattr(narrative_gate, 'require_review', lambda *args: {'runtime_binding': {'reviewed_manifest_sha256': 'changed' if mode == 'bound_changed' else 'approved'}})
    def tick(*args):
        calls.append(('storyboard_tick',))
        if mode == 'admission_errors': raise ValueError('storyboard error')
        return []
    monkeypatch.setattr(storyboard_deployment, 'tick', tick)
    monkeypatch.setattr(repair_rollout_bridge, 'adopt', lambda *args: calls.append(('adopt',)) or [])
    def material_tick(*args):
        calls.append(('material_tick',))
        if mode == 'admission_errors': raise RuntimeError('material error')
        return []
    monkeypatch.setattr(material_production, 'tick', material_tick)
    monkeypatch.setattr(material_production, 'enabled', lambda root: False)
    monkeypatch.setattr(repair, 'storyboard_review_config', lambda: 'storyboard_review.json')
    items = {}
    if mode == 'recovery':
        Path(item['manifest_path']).write_text('{}')
        items = {'request': {**item, 'state': 'preparing'}, 'audit': {'state': 'auditing', 'new_job_id': 'missing'}}
    if mode in ('prepared', 'bound_changed'): items = {'request': {**item, 'state': 'prepared'}}
    if mode in ('render_failed', 'audit_success'): items = {'request': {**item, 'state': 'auditing' if mode == 'audit_success' else 'rendering', 'new_job_id': 'new'}}
    if mode == 'audit_success':
        (out / 'final_audits').mkdir(); (out / 'final_audits/new.json').write_text('{}')
        (tmp_path / 'mix.json').write_text(json.dumps({'source_audio_in_mix': False, 'curated_effects': []}))
        monkeypatch.setattr(repair, 'job_bundle', lambda *args: ({'shots': [{'id': 'S1'}]}, {'quality': {'delivery_policy': {'ready_for_delivery': True}}, 'compose': {'audio_mix_path': str(tmp_path / 'mix.json')}}))
        monkeypatch.setattr(repair, 'audit_file_decision', lambda *args: [])
        monkeypatch.setattr(repair_delivery, 'complete', lambda *args: calls.append(('complete',)) or {'state': 'completed'})
    if mode == 'finished': items = {str(i): {'state': 'completed'} for i in range(30)}
    (out / 'driver_status.json').write_text(json.dumps({'items': items, 'started_at': 1}))
    (out / 'status.json').write_text(json.dumps({'items': [item] if mode == 'prepare' else []}))
    (out / 'previous_delivery_index.json').write_text(json.dumps({'items': [{'product_id': 'product', 'title': 'Title'}]}))
    config = tmp_path / 'research/ascend_worker_pool32.json'; config.parent.mkdir(); config.write_text('{}')
    try:
        repair.main()
    except StopLoop:
        pass
    except RuntimeError as exc:
        assert mode == 'paused'; calls.append(('exception', type(exc).__name__, str(exc)))
    return trace_hash(calls, tmp_path)


@pytest.mark.parametrize('mode', ['empty', 'paused', 'prepare', 'prepared', 'bound_changed', 'recovery', 'render_failed', 'audit_success', 'admission_errors', 'finished'])
def test_repair_driver_state_and_side_effect_order(mode, tmp_path, monkeypatch):
    assert repair_trace(mode, tmp_path, monkeypatch) == REPAIR_GOLDENS[mode]


def deadline_trace(mode, tmp_path, monkeypatch):
    complete = mode == 'complete'
    count = 30 if complete else 1
    jobs = ['job' + str(i) for i in range(count)]
    rows = [{'job_id': job, 'stage': stage, 'status': 'succeeded' if complete else 'pending',
             'result': json.dumps({'delivery_policy': {'ready_for_delivery': True}})}
            for job in jobs for stage in ('quality', 'export')]
    out, calls = install_fakes(deadline, tmp_path, monkeypatch, rows=rows, known=jobs)
    monkeypatch.setattr(deadline, 'preflight', lambda: calls.append(('preflight',)))
    monkeypatch.setattr(deadline, 'paused', lambda root: mode == 'paused')
    topics = [{'source_url': 'https://source.invalid/story', 'title': 'Topic'}] if mode == 'new_topic' else []
    monkeypatch.setattr(deadline, 'ordered_catalogs', lambda: copy.deepcopy(topics))
    from workflow import controller
    class Controller:
        def __init__(self, *args): calls.append(('controller',))
        def admit(self, record):
            calls.append(('admit', record))
            if mode == 'admit_error': raise ValueError('failed admission')
        def tick(self): calls.append(('tick',))
        def items(self): return []
    monkeypatch.setattr(controller, 'Controller', Controller)
    items = {job: {'state': 'finished_planning', 'job_id': job,
             'results': [{'job_id': job, 'state': 'enqueued'}]} for job in jobs}
    if mode == 'empty' or mode == 'new_topic': items = {}
    if mode == 'recovery':
        items = {'old': {'state': 'planning', 'topic_path': str(out / 'old.json')}}
        (out / 'old.result.json').write_text(json.dumps([{'job_id': jobs[0], 'state': 'enqueued'}]))
    (out / 'status.json').write_text(json.dumps({'items': items, 'started_at': 1}))
    try: deadline.main()
    except StopLoop: pass
    return trace_hash(calls, tmp_path)


@pytest.mark.parametrize('mode', ['empty', 'paused', 'recovery', 'admit_error', 'new_topic', 'complete'])
def test_deadline_driver_state_and_side_effect_order(mode, tmp_path, monkeypatch):
    assert deadline_trace(mode, tmp_path, monkeypatch) == DEADLINE_GOLDENS[mode]
