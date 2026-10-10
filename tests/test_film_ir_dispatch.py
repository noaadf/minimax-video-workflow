"""Runner-layer dispatch tests: ssh_generate's film_ir branch end-to-end with the
SSH transport mocked. This is the coverage gap that hid the staged-upload Path+str
crash (astra round-3 P1): the helper was tested, the call site was not."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workflow import runner

CFG = {'type': 'ascend_ssh', 'host': 'user@host.example', 'port': 2222,
       'identity_file': '~/.ssh/id_rsa', 'remote_root': '/remote/root',
       'worker_script': '/remote/worker.py', 'python': 'python3',
       'capability': 'ascend_test', 'generation': {'steps': 50, 'short_edge': 768}}


def make_plan(root):
    kf0, kf1 = Path(root) / 'kf0.png', Path(root) / 'kf1.png'
    kf0.write_bytes(b'start-image-bytes')
    kf1.write_bytes(b'end-image-bytes')
    return {'id': 'production_dispatch_test', 'shot_pipeline': 'film_ir',
            'pacing_policy': 'storyboard_v1',
            'shots': [{'id': 'S01', 'duration_seconds': 6.0, 'prompt': '低饱和情景演绎。',
                       'reference_images': [str(kf0), str(kf1)], 'seed': 11},
                      {'id': 'S02', 'duration_seconds': 4.0, 'source_path': '/asset/s02.mp4'}]}


class FakeTransport:
    """Records ssh/scp invocations and serves a succeeded worker result."""

    def __init__(self):
        self.commands = []
        self.remote = ''

    def __call__(self, *args, **kwargs):
        self.commands.append(list(args))
        command = args[-1] if args else ''
        # ssh commands arrive shlex-joined as the final argument
        if isinstance(command, str) and command.startswith('mkdir -p '):
            self.remote = command[len('mkdir -p '):]
        if isinstance(command, str) and command.startswith('cat '):
            return json.dumps({'task_id': 'production_dispatch_test-S01', 'status': 'succeeded',
                               'video_path': self.remote + '/output/v.mp4'})
        return ''


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.folder = Path(self._dir.name) / 'attempts/S01/token1'
        self.folder.mkdir(parents=True)
        self.plan = make_plan(self._dir.name)
        self.plan['backend'] = CFG
        self.plan['shot_backends'] = {'S01': CFG}  # what backends.assignments produces
        self.transport = FakeTransport()

    def tearDown(self):
        self._dir.cleanup()

    def _ssh_generate(self):
        with mock.patch('workflow.runner.backend_ready', return_value=True), \
                mock.patch('workflow.runner.check_lease'), \
                mock.patch('workflow.media.run', side_effect=self.transport):
            return runner.ssh_generate(self.plan, self.plan['shots'][0],
                                       {'id': 'production_dispatch_test:S01'}, self.folder)

    def test_film_ir_dispatch_stages_refs_and_persists_manifest(self):
        path, metrics = self._ssh_generate()
        self.assertTrue(Path(path).name == 'generated.mp4')
        staged = sorted(p.name for p in (self.folder / 'refs').iterdir())
        self.assertEqual(staged, ['reference_0.png', 'reference_1.png'])
        request = json.loads((self.folder / 'request.json').read_text())
        self.assertEqual(request['task_id'], 'production_dispatch_test-S01')
        self.assertEqual([Path(r).name for r in request['reference_images']],
                         ['reference_0.png', 'reference_1.png'])
        self.assertTrue(all(r.startswith('/remote/root/tasks/') for r in request['reference_images']))
        manifest = json.loads((self.folder / 'film_ir_manifest.json').read_text())
        self.assertEqual(manifest['shots'][0]['id'], 'S01')
        # the worker command ran once, AFTER staging and request upload
        worker_calls = [c for c in self.transport.commands
                        if any(isinstance(a, str) and '--request' in a for a in c)]
        self.assertEqual(len(worker_calls), 1)

    def test_staging_failure_stops_before_worker(self):
        # A keyframe content mismatch at staging must abort BEFORE the worker runs
        # (the call-site wiring the helper test cannot see).
        from workflow import film_ir_plan
        with mock.patch.object(film_ir_plan, 'stage_reference',
                               side_effect=ValueError('keyframe content changed since compile')):
            with self.assertRaises(ValueError):
                self._ssh_generate()
        self.assertFalse(any(isinstance(a, str) and '--request' in a
                             for c in self.transport.commands for a in c))
        self.assertNotIn('request.json', [p.name for p in self.folder.iterdir()])

    def test_legacy_plan_uploads_plan_refs_without_staging(self):
        plan = {**self.plan, 'shot_pipeline': 'legacy'}
        with mock.patch('workflow.runner.backend_ready', return_value=True), \
                mock.patch('workflow.runner.check_lease'), \
                mock.patch('workflow.media.run', side_effect=self.transport):
            runner.ssh_generate(plan, plan['shots'][0],
                                {'id': 'production_dispatch_test:S01'}, self.folder)
        self.assertFalse((self.folder / 'refs').exists())
        self.assertFalse((self.folder / 'film_ir_manifest.json').exists())
        request = json.loads((self.folder / 'request.json').read_text())
        self.assertEqual([Path(r).name for r in request['reference_images']],
                         ['reference_0.png', 'reference_1.png'])


if __name__ == '__main__':
    unittest.main()
