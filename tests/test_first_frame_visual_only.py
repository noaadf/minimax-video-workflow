import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

from workflow import first_frame as ff, storyboard
from workflow.backends import supports_shot
from workflow.plan import write_json, sha256
from workflow.storyboard_image_jobs import ImageJobs
from workflow.runner import visual_generation_request
from tests.test_storyboard_pipeline import film

ROOT = Path('research/ascend_worker_single_first_frame_20261003')


def plan():
    p = film()
    p.update(image_generation_strategy=ff.STRATEGY, use_native_foley=False,
             audio_policy={'source_audio_enabled':False}, shared_visual_spec={'subject':'one fixed cargo vehicle'})
    for s in p['shots']:
        s.update(generation_contract=copy.deepcopy(ff.CONTRACT),keyframe_prompts={'start':'A reviewed start state.'})
    return p


class FirstFramePolicyTests(unittest.TestCase):
    def test_only_explicit_new_strategy_changes_reference_requirement(self):
        old=film();new=plan()
        self.assertEqual(ff.reference_roles(old,old['shots'][0]),['start','end'])
        self.assertEqual(ff.reference_roles(new,new['shots'][0]),['start'])
        old['shots'][0]['generation_contract']=ff.CONTRACT.copy()
        with self.assertRaisesRegex(ValueError,'new explicit'):ff.validate_policy(old)

    def test_no_tail_or_native_audio_or_missing_contract(self):
        for mutate in (lambda p:p.update(use_native_foley=True),
                       lambda p:p['audio_policy'].update(source_audio_enabled=True),
                       lambda p:p['shots'][0].update(reference_images=['a','b']),
                       lambda p:p['shots'][0]['keyframe_prompts'].update(end='fake tail'),
                       lambda p:p['shots'][0].pop('generation_contract')):
            p=plan();mutate(p)
            with self.assertRaises(ValueError):ff.validate_policy(p)

    def test_image_jobs_and_missing_assets_require_real_start_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=plan();path=root/'manifest.json';write_json(path,p)
            jobs=ImageJobs(root/'jobs');jobs.add(path,'new')
            rows=[json.loads(f.read_text()) for f in (root/'jobs').glob('*.json')]
            self.assertEqual([(r['shot_id'],r['role']) for r in rows],[('S1','start'),('S2','start')])
            for s in p['shots']:
                image=root/(s['id']+'.png');Image.new('RGB',(36,64),'blue').save(image)
                s.update(previsual_path=str(image),reference_images=[str(image)])
            self.assertEqual(storyboard.missing_previsuals(p),[])
            p.pop('image_generation_strategy')
            for s in p['shots']:s.pop('generation_contract')
            self.assertTrue(any('2 start/end' in s for s in storyboard.missing_previsuals(p)))

    def test_only_bound_slot_can_accept_single_frame(self):
        cfg=json.loads((ROOT/'backend_opt_in.json').read_text());s=plan()['shots'][0];s['reference_images']=['first.png']
        self.assertTrue(supports_shot(cfg,s))
        for mutate in (lambda c:c.update(capability='another_slot'),lambda c:c.pop('first_frame_visual_only_validation'),
                       lambda c:c.update(required_visual_consumer_version='old')):
            c=copy.deepcopy(cfg);mutate(c);self.assertFalse(supports_shot(c,s))
        self.assertEqual(cfg['validated_reference_image_counts'],[2])

    def test_start_image_claim_does_not_invent_cross_shot_reference(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=plan();path=root/'manifest.json';write_json(path,p)
            jobs=ImageJobs(root/'jobs');jobs.add(path,'new')
            first=jobs.claim('new');image=root/'first.png';Image.new('RGB',(36,64),'blue').save(image)
            jobs.complete(first['id'],first['claim_token'],image)
            second=jobs.claim('new')
            self.assertEqual(second['reference_images'],[])
            self.assertFalse(second['image_conditioning_applied'])

    def test_new_runtime_receipt_is_required(self):
        expected=ff.runtime_requirements()
        self.assertEqual(ff.require_runtime(expected,expected),expected)
        with self.assertRaises(ValueError):ff.require_runtime({},expected)
        with self.assertRaises(ValueError):ff.require_runtime(expected,{**expected,'loaded_runner_sha256':'stale'})

    def test_request_rejects_unproved_profile_before_ssh(self):
        p=plan();s=p['shots'][0];s['reference_images']=['x.png']
        with self.assertRaises(ValueError):visual_generation_request(p,s,{'id':'x:S1'},{'remote_root':'/worker'})

    def test_source_timing_rejects_bad_whole_action_before_cloud_review(self):
        from workflow.storyboard_downstream import validate_source_timing, Paused
        p=plan();s=p['shots'][0]
        s.update(source_path=str(ROOT/'probe.mp4'), source_fit_policy='whole_action', duration_seconds=2)
        with self.assertRaises(Paused) as raised:validate_source_timing(p)
        self.assertEqual(raised.exception.state,'isolated_source_timing')
        self.assertEqual(raised.exception.detail['shot_id'],'S1')
        s['duration_seconds']=4.4
        validate_source_timing(p)


class VisualOnlyReconciliationTests(unittest.TestCase):
    def fixture(self):
        cfg=json.loads((ROOT/'backend_opt_in.json').read_text())
        req=json.loads((ROOT/'request.json').read_text())
        result=json.loads((ROOT/'worker_result.json').read_text())
        ev=json.loads((ROOT/'local_visual_consumer_test/remote_evidence.json').read_text())
        return cfg,req,result,ev

    def verify(self,cfg,req,result,ev,dest):
        return ff.verify_and_strip(ROOT/'probe.mp4',dest,req,result,ev,cfg,
            expected_reference_sha256=[json.loads((ROOT/'adapter_evidence.json').read_text())['reference_sha256']])

    def test_real_silent_raw_becomes_video_only_not_content_approval(self):
        cfg,req,result,ev=self.fixture()
        with tempfile.TemporaryDirectory() as d:
            receipt=self.verify(cfg,req,result,ev,Path(d)/'video.mp4')
        self.assertEqual(receipt['status'],'visual_only_technical_passed')
        self.assertFalse(receipt['content_approved']);self.assertFalse(receipt['native_audio_consumed'])
        self.assertEqual(receipt['worker_failure_preserved'],result['error'])

    def test_all_other_worker_errors_fail_closed(self):
        cfg,req,result,ev=self.fixture()
        for error in ({'code':'INVALID_OUTPUT','message':'decode_errors: bad','retryable':False},
                      {'code':'INVALID_OUTPUT','message':'audio_silent_or_undecodable: max_volume=-76.3 dB','retryable':True},
                      {'code':'POLL_TIMEOUT','message':'unknown','retryable':True}):
            r={**result,'error':error}
            with self.assertRaises(ValueError):self.verify(cfg,req,r,ev,Path('/unused.mp4'))

    def test_changed_sha_task_source_or_ambiguous_submission_fails_before_decode(self):
        cfg,req,result,ev=self.fixture()
        for mutate in (lambda e:e.update(raw_sha256='changed'),lambda e:e['state'].update(task_id='wrong'),
                       lambda e:e['state'].update(submission_ambiguous=True),
                       lambda e:e.update(reference_sha256=['wrong']),
                       lambda e:e.update(worker_sha256='different worker'),
                       lambda e:e['server'].update(status='queued')):
            e=copy.deepcopy(ev);mutate(e)
            with patch('workflow.first_frame.subprocess.run') as run:
                with self.assertRaises(ValueError):self.verify(cfg,req,result,e,Path('/unused.mp4'))
                run.assert_not_called()

    def test_unreadable_missing_or_wrong_duration_audio_is_not_silence(self):
        cfg,req,result,ev=self.fixture()
        info=json.loads((ROOT/'ffprobe.json').read_text())
        for mutate in (lambda i:i.update(streams=[s for s in i['streams'] if s['codec_type']=='video']),
                       lambda i:i['format'].update(duration='9'),
                       lambda i:i['streams'][0].update(width=1344,height=768)):
            i=copy.deepcopy(info);mutate(i)
            with patch('workflow.media.probe',return_value=i):
                with self.assertRaises(ValueError):self.verify(cfg,req,result,ev,Path('/unused.mp4'))
        with patch('workflow.media.probe',return_value=info), patch('workflow.first_frame.subprocess.run') as run:
            run.return_value.returncode=1;run.return_value.stderr='bad frame';run.return_value.stdout=''
            with self.assertRaisesRegex(ValueError,'decode'):self.verify(cfg,req,result,ev,Path('/unused.mp4'))

    def test_remote_reconciler_is_read_only_and_never_retries_worker(self):
        cfg,req,_,_=self.fixture();code=ff.remote_evidence_script(req,cfg,failed=True)
        compile(code,'read_only_probe','exec')
        self.assertNotIn('POST',code);self.assertNotIn('subprocess',code)
        self.assertIn('video.mp4.part',code)
