import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from workflow.cloud_llm import request_identity, status, submit
from workflow.plan import write_json


class CloudCommitRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.request = {'model': 'glm-5.3', 'max_tokens': 12000,
                        'instruction': 'Return JSON', 'data': {}, 'images': []}
        self.jid = request_identity(self.request)
        self.spawn = Mock(return_value=Mock(pid=123))
        submit(self.root, self.request, self.spawn)
        self.folder = self.root / 'jobs' / self.jid
        self.result = {'answer': {'verdict': 'reject', 'findings': []},
                       'receipt': {'execution': 'cloud', 'cloud_job_id': self.jid,
                                   'model': 'glm-5.3'}}

    def state(self, name):
        path = self.folder / 'status.json'
        value = json.loads(path.read_text())
        value['state'] = name
        write_json(path, value)
        return path.read_bytes()

    def commit(self, result=None):
        write_json(self.folder / 'result.json', self.result if result is None else result)

    def test_committed_result_recovers_stale_states_without_replay_or_state_write(self):
        for old in ('submitted', 'waiting_slot', 'calling', 'failed'):
            with self.subTest(old=old):
                before = self.state(old)
                self.commit()
                view = status(self.root, self.jid)
                self.assertEqual(view['state'], 'completed')
                self.assertEqual(view['recovered_from_state'], old)
                self.assertEqual(view['result'], self.result)
                self.assertEqual((self.folder / 'status.json').read_bytes(), before)
                submit(self.root, self.request, self.spawn)
                self.spawn.assert_called_once()

    def test_normal_completed_result_keeps_rejection_and_receipt(self):
        self.state('completed')
        self.commit()
        view = status(self.root, self.jid)
        self.assertEqual(view['state'], 'completed')
        self.assertEqual(view['result']['answer']['verdict'], 'reject')
        self.assertEqual(view['result']['receipt']['cloud_job_id'], self.jid)
        self.assertNotIn('recovered_from_state', view)

    def test_incomplete_or_wrong_request_result_cannot_mark_completed(self):
        for result in ({}, {'answer': {}}, {'receipt': self.result['receipt']},
                       {'answer': {}, 'receipt': None},
                       {'answer': {}, 'receipt': {'execution': 'cloud', 'cloud_job_id': '0' * 64}},
                       {'answer': {}, 'receipt': {'execution': 'other', 'cloud_job_id': self.jid}}):
            with self.subTest(result=result):
                before = self.state('calling')
                self.commit(result)
                with self.assertRaises(ValueError):
                    status(self.root, self.jid)
                self.assertEqual((self.folder / 'status.json').read_bytes(), before)
                self.spawn.assert_called_once()

    def test_mismatched_status_identity_is_rejected(self):
        value = json.loads((self.folder / 'status.json').read_text())
        value['id'] = '0' * 64
        write_json(self.folder / 'status.json', value)
        self.commit()
        with self.assertRaises(ValueError):
            status(self.root, self.jid)

    def test_absent_or_partial_result_never_becomes_completed(self):
        for old in ('submitted', 'calling', 'failed'):
            self.state(old)
            self.assertEqual(status(self.root, self.jid)['state'], old)
        self.state('calling')
        (self.folder / 'result.json').write_text('{"answer":')
        with self.assertRaises(ValueError):
            status(self.root, self.jid)
        self.spawn.assert_called_once()

    def test_request_change_cannot_reuse_committed_result(self):
        self.state('calling')
        self.commit()
        changed = copy.deepcopy(self.request)
        changed['data'] = {'different': True}
        other = request_identity(changed)
        self.assertNotEqual(other, self.jid)
        with self.assertRaises(FileNotFoundError):
            status(self.root, other)

    def test_client_consumes_recovered_result_after_one_poll_without_resubmission(self):
        from workflow.cloud_llm_client import ask
        self.state('calling')
        self.commit()
        actions = []

        def transport(argv, **kwargs):
            action = 'submit' if ' submit ' in argv[-1] else 'status'
            actions.append(action)
            self.assertLessEqual(len(actions), 2, 'Client kept polling a committed result')
            if action == 'submit':
                request = json.loads(kwargs['input'])
                self.assertEqual(request_identity(request), self.jid)
                value = submit(self.root, request, self.spawn)
            else:
                value = status(self.root, self.jid)
            return Mock(returncode=0, stdout=json.dumps(value))

        config = {'model': self.request['model'], 'cloud_worker':
                  {'host': 'synthetic', 'port': 22, 'identity_file': str(self.root / 'unused'),
                   'code_root': '/synthetic', 'state_root': str(self.root), 'wait_seconds': 30}}
        with patch('workflow.cloud_llm_client.subprocess.run', side_effect=transport), \
             patch('workflow.cloud_llm_client.time.sleep'):
            answer, receipt = ask(config, self.request['instruction'], self.request['data'],
                                  self.request['max_tokens'])
        self.assertEqual(answer, self.result['answer'])
        self.assertEqual(receipt, self.result['receipt'])
        self.assertEqual(actions, ['submit', 'status'])
        self.spawn.assert_called_once()
