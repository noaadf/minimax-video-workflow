import fcntl,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from workflow.queue import Queue
from workflow.plan import write_json,sha256,digest
from workflow.material_production import tick
from workflow.material_preparation import verified_request

class IntakeTests(unittest.TestCase):
    def setUp(self):
        from workflow.material_production import _CHILDREN
        _CHILDREN.clear()
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.q=Queue(self.root/'queue.sqlite')
        self.lock=open(self.q.path+'.controller-service.lock','a');self.addCleanup(self.lock.close)
        fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        write_json(self.root/'backend.json',{'type':'assets'})
        write_json(self.root/'material_intake/production_policy.json',{'enabled':True,'max_preparing':2,'backend_file':str(self.root/'backend.json'),'review_config':'unused'})
    def request(self,cid='good'):
        p=self.root/'material_intake'/cid/'production_request.json';write_json(p,{'content_id':cid})
        m=p.with_name('manifest.json');write_json(m,{'title':cid})
        return p,{'content_id':cid,'manifest_path':str(m)}
    def state(self):return json.loads((self.root/'material_intake/production/state.json').read_text())
    def test_only_existing_owner_can_mutate(self):
        self.request()
        with self.assertRaisesRegex(ValueError,'singleton'):tick(self.q,self.root,None)
        self.assertFalse((self.root/'material_intake/production/state.json').exists())
    def test_bad_request_does_not_starve_good(self):
        self.request('bad');_,r=self.request('good');child=Mock(pid=111);child.poll.return_value=None
        def verify(path,*args):
            if Path(path).parent.name=='bad':raise ValueError('changed evidence')
            return r
        with patch('workflow.material_production.verified_request',side_effect=verify),patch('workflow.material_production.subprocess.Popen',return_value=child):
            self.assertEqual(tick(self.q,self.root,self.lock.fileno()),[])
        items={x['content_id']:x for x in self.state()['items'].values()}
        self.assertEqual(items['bad']['state'],'attention');self.assertEqual(items['good']['state'],'preparing');self.assertEqual(self.q.status(),[])
    def test_interrupted_launch_never_replays(self):
        path,r=self.request();key=digest(['good',sha256(path)])[:24]
        folder=self.root/'material_intake/production'/key
        write_json(folder.parent/'state.json',{'items':{key:{'content_id':'good','state':'launch_intent','request_path':str(path),'request_sha256':sha256(path),'folder':str(folder)}}})
        with patch('workflow.material_production.verified_request',return_value=r),patch('workflow.material_production.subprocess.Popen') as launch:
            tick(self.q,self.root,self.lock.fileno());tick(self.q,self.root,self.lock.fileno())
        launch.assert_not_called();self.assertEqual(self.state()['items'][key]['state'],'attention')
    def test_preparation_limit_and_no_admission_before_media(self):
        requests={}
        for cid in ('a','b','c'):
            p,r=self.request(cid);requests[cid]=r
        child=Mock(pid=123);child.poll.return_value=None
        with patch('workflow.material_production.verified_request',side_effect=lambda p,*a:requests[Path(p).parent.name]),patch('workflow.material_production.subprocess.Popen',return_value=child) as launch:
            tick(self.q,self.root,self.lock.fileno());tick(self.q,self.root,self.lock.fileno())
        self.assertEqual(launch.call_count,2);self.assertEqual(self.q.status(),[])
    def test_unknown_worker_is_attention_not_pending(self):
        path,r=self.request();key=digest(['good',sha256(path)])[:24];folder=self.root/'material_intake/production'/key
        write_json(folder.parent/'state.json',{'items':{key:{'content_id':'good','state':'preparing','pid':99999999,'request_path':str(path),'request_sha256':sha256(path),'folder':str(folder)}}})
        with patch('workflow.material_production.verified_request',return_value=r),patch('workflow.material_production.subprocess.Popen') as launch:
            tick(self.q,self.root,self.lock.fileno())
        launch.assert_not_called();self.assertEqual(self.state()['items'][key]['state'],'attention')
    def test_completed_worker_with_failed_preflight_cannot_submit(self):
        path,r=self.request();key=digest(['good',sha256(path)])[:24];folder=self.root/'material_intake/production'/key
        write_json(folder/'result.json',{'state':'ready','request_sha256':sha256(path)})
        write_json(folder.parent/'state.json',{'items':{key:{'content_id':'good','state':'preparing','pid':99999999,'request_path':str(path),'request_sha256':sha256(path),'folder':str(folder)}}})
        with patch('workflow.material_production.verified_request',return_value=r),patch('workflow.material_production.verify_ready',side_effect=ValueError('Actual preflight failed')),patch('workflow.runner.submit') as submit:
            tick(self.q,self.root,self.lock.fileno())
        submit.assert_not_called();self.assertEqual(self.state()['items'][key]['state'],'attention')
    def test_admission_commit_window_recovers_same_job(self):
        path,r=self.request();key=digest(['good',sha256(path)])[:24];folder=self.root/'material_intake/production'/key
        plan={'id':'materialjob','material_origin':{'content_id':'good'},'approved_story_preflight':{},'input_sha256':{}}
        self.q.submit('materialjob','test',plan,[{'id':'materialjob:shot','stage':'shot','capability':'cpu','payload':{},'deps':[]}])
        result=folder/'result.json';write_json(result,{'state':'ready','job_id':'materialjob','manifest_path':'unused'})
        write_json(folder.parent/'state.json',{'items':{key:{'content_id':'good','state':'admitting','job_id':'materialjob','request_path':str(path),'request_sha256':sha256(path),'folder':str(folder),'result_path':str(result),'result_sha256':sha256(result)}}})
        with patch('workflow.material_production.verified_request',return_value=r),patch('workflow.material_production.verify_ready',return_value=plan),patch('workflow.runner.submit') as submit:
            self.assertEqual(tick(self.q,self.root,self.lock.fileno()),['materialjob']);self.assertEqual(tick(self.q,self.root,self.lock.fileno()),['materialjob'])
        submit.assert_not_called()
        with self.q.connect() as c:self.assertEqual(c.execute('select count(*) from production_items').fetchone()[0],1)
    def test_changed_prepared_version_blocks_existing_job_recovery(self):
        path,r=self.request();key=digest(['good',sha256(path)])[:24];folder=self.root/'material_intake/production'/key
        self.q.submit('materialjob','test',{'material_origin':{'content_id':'other'}},[])
        result=folder/'result.json';write_json(result,{'state':'ready','job_id':'materialjob','manifest_path':'unused'})
        write_json(folder.parent/'state.json',{'items':{key:{'content_id':'good','state':'admitting','request_path':str(path),'request_sha256':sha256(path),'folder':str(folder),'result_path':str(result),'result_sha256':sha256(result)}}})
        with patch('workflow.material_production.verified_request',return_value=r),patch('workflow.material_production.verify_ready',return_value={'material_origin':{'content_id':'good'}}),patch('workflow.runner.submit') as submit:
            self.assertEqual(tick(self.q,self.root,self.lock.fileno()),[])
        submit.assert_not_called();self.assertEqual(self.state()['items'][key]['state'],'attention')
    def test_manifest_tampering_is_rejected_before_review(self):
        p,_=self.request();data={'content_id':'good','state':'awaiting_storyboard'}
        for key in ('manifest','narrative_review','keyframe_request'):
            f=p.with_name(key+'.json');write_json(f,{});data.update({key+'_path':str(f),key+'_sha256':sha256(f)})
        write_json(p,data);write_json(data['manifest_path'],{'changed':True})
        with patch('workflow.narrative_gate.require_review') as review:
            with self.assertRaisesRegex(ValueError,'evidence changed'):verified_request(p)
        review.assert_not_called()

class NaturalTimingTests(unittest.TestCase):
    def plan(self):
        from tests.test_storyboard_pipeline import film
        p=film();p['narration']=[{'start':.2,'end':7,'text':'先装货，','fact_ids':[], 'caption_chunks':['先装货，'],'caption_lines':[['先装货，']]},
            {'start':8,'end':14,'text':'再固定。','fact_ids':[],'caption_chunks':['再固定。'],'caption_lines':[['再固定。']]}]
        return p
    def test_whole_voice_preserves_words_and_measured_cuts(self):
        from workflow.material_preparation import timed_plan
        with tempfile.TemporaryDirectory() as tmp:
            timing=Path(tmp)/'words.json';write_json(timing,{})
            points={0:(0,1),1:(1,2),2:(2,3),4:(6,7),5:(7,8),6:(8,9)}
            with patch('workflow.speech_timing.align_script',return_value=(points,{'exact_match_ratio':1})),patch('workflow.media.duration',return_value=14):
                p,measurement=timed_plan(self.plan(),Path(tmp)/'whole.mp3',timing)
            self.assertEqual(p['narration'][0]['text'],'先装货，再固定。')
            self.assertEqual(len(p['narration']),1);self.assertEqual(p['shots'][0]['duration_seconds'],4.75)
            self.assertEqual(p['narration'][0]['caption_tempo'],1);self.assertGreaterEqual(measurement['musical_tail'],2)
            self.assertEqual(sum(s['duration_seconds'] for s in p['shots']),p['duration_seconds'])
    def test_source_window_cannot_be_lengthened_to_fit_speech(self):
        from workflow.material_preparation import timed_plan
        with tempfile.TemporaryDirectory() as tmp:
            timing=Path(tmp)/'words.json';write_json(timing,{})
            p=self.plan()
            for s in p['shots']:s['source_path']='/missing-original.mp4'
            with patch('workflow.speech_timing.align_script',return_value=({},{})),patch('workflow.media.duration',return_value=15):
                with self.assertRaisesRegex(ValueError,'source windows'):timed_plan(p,Path(tmp)/'whole.mp3',timing)

class EndpointAuthoringTests(unittest.TestCase):
    def test_static_endpoints_are_separate_and_source_bound(self):
        from tests.test_storyboard_pipeline import film
        from workflow.material_preparation import author_keyframes
        p=film();answer={'shared_visual_spec':{'identity':'same fictional buyer'},'frames':[{'shot_id':s['id'],'start':'one static uncompleted state','end':'one static completed state'} for s in p['shots']]}
        with tempfile.TemporaryDirectory() as tmp:
            write_json(Path(tmp)/'keyframe_authoring.json',{'original_manifest_sha256':'approved','answer':answer})
            revised=author_keyframes(p,Path(tmp),'approved')
            self.assertEqual(revised['shots'][0]['keyframe_prompts']['start'],'one static uncompleted state')
            self.assertNotIn('lowers',revised['shots'][0]['keyframe_prompts']['start'])
            with self.assertRaisesRegex(ValueError,'another approved'):author_keyframes(p,Path(tmp),'other')
    def test_partial_endpoint_set_is_not_used(self):
        from tests.test_storyboard_pipeline import film
        from workflow.material_preparation import author_keyframes
        with tempfile.TemporaryDirectory() as tmp:
            write_json(Path(tmp)/'keyframe_authoring.json',{'original_manifest_sha256':'approved','answer':{'shared_visual_spec':{'identity':'same'},'frames':[]}})
            with self.assertRaisesRegex(ValueError,'each generated scene'):author_keyframes(film(),Path(tmp),'approved')
    def test_interrupted_authoring_does_not_repeat_model(self):
        from tests.test_storyboard_pipeline import film
        from workflow.material_preparation import author_keyframes
        with tempfile.TemporaryDirectory() as tmp,patch('workflow.llm.ask_json') as ask:
            write_json(Path(tmp)/'keyframe_authoring_attempt.json',{'state':'attempting'})
            with self.assertRaisesRegex(ValueError,'receipt reconciliation'):author_keyframes(film(),Path(tmp),'approved')
        ask.assert_not_called()
    def test_actual_negative_picture_check_blocks_ready_version(self):
        from workflow.material_production import verify_ready
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);write_json(folder/'manual_picture_inspection.json',{'verdict':'reject'})
            with self.assertRaisesRegex(ValueError,'keyframe inspection rejected'):verify_ready({},folder,'approved')
