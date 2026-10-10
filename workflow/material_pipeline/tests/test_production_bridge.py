import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from workflow.material_pipeline import production_bridge as bridge
from workflow.material_pipeline.core import content_id, enqueue_candidate, fact_id, sha256, write_json
from workflow.material_pipeline.refresh_feed import refresh
from workflow.material_pipeline.tests.test_evidence import EvidenceTest
from workflow.queue import Queue
from tests.test_storyboard_pipeline import film
from workflow.narrative_gate import CHECKS, require_review


class ProductionBridgeTest(unittest.TestCase):
    def fixture(self, directory):
        root=Path(directory)/'material';root.mkdir()
        prod=Path(directory)/'production';prod.mkdir();Queue(prod/'queue.sqlite')
        package=EvidenceTest().fixture(root)
        package['assets'][0]['duration']=16
        package['assets'][0]['rights']={'status':'original','owner':'TEST_ONLY'}
        day=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
        claims=['团队完成120余次试验。','试验检验了设备的稳定性。','团队完成了实验阶段的测试。']
        body='发布时间：'+day+'\n'+'\n'.join(claims)
        for name in ('source.html','source.txt'):(root/name).write_text(body)
        package['event']['date']=day;package['content_id']=content_id(package['event'])
        package['sources'][0].update(published=day,date_evidence='发布时间：'+day,
            sha256=sha256(root/'source.html'),text_sha256=sha256(root/'source.txt'))
        base=package['facts'][0];package['facts']=[]
        for i,claim in enumerate(claims):
            f=copy.deepcopy(base);start=body.index(claim)
            f.update(id='F'+str(i+1),text=claim,predicate=claim,object=claim)
            f['evidence'][0].update(quote=claim,start=start,end=start+len(claim))
            f['fingerprint']=fact_id(f);package['facts'].append(f)
        package['editorial']['beats'][0]['fact_ids']=[f['id'] for f in package['facts']]
        enqueue_candidate(package,root);refresh(root)
        config=Path(directory)/'config.json';write_json(config,{'music':{'path':'fixture.wav','license_note':'test fixture'}})
        write_json(root/'production_bridge.json',{'enabled':True,'production_root':str(prod),
            'production_config':str(config),'planning_config':str(config),'max_plans_per_cycle':0})
        return root,prod,package

    def review(self,prompt,data,**kw):
        if 'fact_checks' in prompt:
            assert 'editorial' not in data and 'event' not in data
            s=data['sources'][0];quotes=s['text'].splitlines()[1:]
            story={'verdict':'pass','kind':'engineering_trial','actual_case':True,'theme_relevant':True,
                   'feature_list_only':False,'invented_story_required':False,'actors':'TEST_ONLY actual team',
                   'audience_value':'TEST_ONLY equipment stability matters'}
            for role,quote in zip(('problem','action','outcome'),quotes):
                story[role]={'claim':'TEST_ONLY '+role,'evidence':[{'source_id':s['id'],'quote':quote}]}
            story['outcome']['modality']='completed_stage'
            return {'verdict':'pass','source_story':story,'issues':[],'fact_checks':[{'id':f['id'],'verdict':'supported'} for f in data['facts']]}, {'model':'TEST_ONLY','usage':{}}
        return {'verdict':'pass','issues':[],'checks':{k:True for k in (*CHECKS,'source_story_fidelity')}}, {'model':'TEST_ONLY','usage':{}}

    def plan(self,brief,path):
        p=film();p.update(id=brief['id'],facts=brief['facts'],music=brief['music'])
        write_json(path,p);return p

    def test_real_batch_bridge_hashes_and_no_video_dispatch(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d)
            with patch('workflow.batch.submit') as submit:
                result=bridge.run(root,prod,1,reviewer=self.review,planner=self.plan)
                submit.assert_not_called()
            self.assertEqual(result['items'][0]['state'],'awaiting_storyboard')
            request=json.loads((prod/'material_intake'/p['content_id']/'production_request.json').read_text())
            require_review(request['manifest_path'],request['narrative_review_path'])
            keys=json.loads(Path(request['keyframe_request_path']).read_text())
            self.assertEqual(keys['manifest_sha256'],request['manifest_sha256'])
            self.assertFalse(request['production_ready'])
            with bridge.connection(prod/'queue.sqlite') as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
                self.assertEqual(c.execute('SELECT state FROM editorial_items').fetchone()[0],'awaiting_storyboard')
            reviewer=Mock(side_effect=AssertionError('repeat external request'))
            bridge.run(root,prod,1,reviewer=reviewer,planner=self.plan);reviewer.assert_not_called()

    def test_model_failure_is_durable_and_not_blindly_repeated(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d);review=Mock(side_effect=TimeoutError('unknown remote outcome'));producer=Mock()
            bridge.run(root,prod,1,reviewer=review,producer=producer,planner=self.plan)
            result=bridge.run(root,prod,1,reviewer=review,producer=producer,planner=self.plan)
            self.assertEqual(review.call_count,1);producer.assert_not_called()
            self.assertEqual(result['items'][0]['state'],'material_attention')
            self.assertTrue((prod/'material_intake'/p['content_id']/'review_attempt.json').exists())

    def test_new_intake_survives_renderer_write_contention_without_duplicates(self):
        import sqlite3
        import threading
        from contextlib import contextmanager

        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d)
            database=prod/'queue.sqlite';bridge.initialize(database)
            acquired=threading.Event();errors=[]

            def competing_writer():
                try:
                    with sqlite3.connect(database) as c:
                        c.execute('BEGIN IMMEDIATE');acquired.set()
                        # Longer than the shared helper's first acquisition,
                        # forcing a real SQLITE_BUSY retry before row writes.
                        threading.Event().wait(2.3)
                except Exception as exc:
                    errors.append(exc);acquired.set()

            original_connection=bridge.connection
            @contextmanager
            def impatient_connection(path):
                with original_connection(path) as c:
                    c.execute('PRAGMA busy_timeout=1')
                    yield c

            writer=threading.Thread(target=competing_writer);writer.start()
            self.assertTrue(acquired.wait(2))
            try:
                with patch.object(bridge,'connection',impatient_connection):
                    first=bridge.synchronize(root,prod)
                    second=bridge.synchronize(root,prod)
            finally:
                writer.join(4)
            self.assertFalse(writer.is_alive());self.assertEqual(errors,[])
            self.assertFalse(first[0]['duplicate']);self.assertTrue(second[0]['duplicate'])
            with original_connection(database) as c:
                self.assertEqual(c.execute('SELECT count(*) FROM material_intakes').fetchone()[0],1)
                self.assertEqual(c.execute('SELECT count(*) FROM editorial_backlog').fetchone()[0],1)
                self.assertEqual(c.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
                self.assertEqual(c.execute('SELECT count(*) FROM tasks').fetchone()[0],0)

    def test_interrupted_stage_requires_audit(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d);bridge.synchronize(root,prod)
            bridge.set_state(prod/'queue.sqlite',p['content_id'],'production_planning',{'attempt':'fixture'})
            reviewer=Mock();result=bridge.run(root,prod,1,reviewer=reviewer,planner=self.plan)
            reviewer.assert_not_called();self.assertEqual(result['items'][0]['state'],'material_attention')

    def test_bad_fact_check_blocks_planning(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d);producer=Mock()
            review=lambda *a,**kw:({'verdict':'pass','issues':[],'fact_checks':[]},{'model':'TEST_ONLY','usage':{}})
            result=bridge.run(root,prod,1,reviewer=review,producer=producer,planner=self.plan)
            producer.assert_not_called();self.assertEqual(result['items'][0]['state'],'material_needs_revision')

    def test_factually_supported_features_are_not_a_story(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d);producer=Mock()
            def review(prompt,data,**kw):
                answer,receipt=self.review(prompt,data,**kw)
                answer['source_story'].update(feature_list_only=True,actual_case=False)
                return answer,receipt
            result=bridge.run(root,prod,1,reviewer=review,producer=producer)
            producer.assert_not_called();self.assertEqual(result['items'][0]['state'],'material_needs_revision')
            self.assertFalse((prod/'material_intake'/p['content_id']/'production_request.json').exists())

    def test_invented_role_quote_cannot_supply_a_story(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d);producer=Mock()
            def review(prompt,data,**kw):
                answer,receipt=self.review(prompt,data,**kw)
                answer['source_story']['action']['evidence'][0]['quote']='虚构开发者整理论文之后完成实验。'
                return answer,receipt
            result=bridge.run(root,prod,1,reviewer=review,producer=producer)
            producer.assert_not_called();self.assertEqual(result['items'][0]['state'],'material_needs_revision')

    def test_stale_feed_and_main_state_cannot_restore_withdrawal(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d)
            bridge.run(root,prod,1,reviewer=self.review,planner=self.plan)
            path=root/'inbox'/p['content_id']/'package.json'
            write_json(root/'editorial_dispositions.json',{p['content_id']:{'status':'withdrawn','package_sha256':sha256(path),'reason':'Rejected original case'}})
            req=prod/'material_intake'/p['content_id']/'production_request.json'
            write_json(prod/'material_intake/production/state.json',{'items':{'stale':{'content_id':p['content_id'],'request_sha256':sha256(req),'state':'prepared'}}})
            reviewer=Mock();result=bridge.run(root,prod,1,reviewer=reviewer)
            reviewer.assert_not_called();self.assertEqual(result['items'][0]['state'],'withdrawn')
            from workflow.material_preparation import verified_request
            with self.assertRaisesRegex(ValueError,'withdrawn'):verified_request(req,Queue(prod/'queue.sqlite'))

    def test_legacy_fact_approval_cannot_start_preparation(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d)
            bridge.run(root,prod,1,reviewer=self.review,planner=self.plan)
            req=prod/'material_intake'/p['content_id']/'production_request.json'
            from workflow.material_preparation import verified_request
            verified_request(req,Queue(prod/'queue.sqlite'))
            review_path=req.with_name('material_review.json');old=json.loads(review_path.read_text());old.pop('schema_version')
            write_json(review_path,old);request=json.loads(req.read_text());request['story_review_sha256']=sha256(review_path);write_json(req,request)
            with self.assertRaisesRegex(ValueError,'fact-only legacy'):verified_request(req)

    def test_source_review_change_blocks_preparation(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d)
            bridge.run(root,prod,1,reviewer=self.review,planner=self.plan)
            req=prod/'material_intake'/p['content_id']/'production_request.json'
            old=req.with_name('material_review.json');old.write_text(old.read_text()+' ')
            from workflow.material_preparation import verified_request
            with self.assertRaisesRegex(ValueError,'Bound source'):verified_request(req)

    def test_excluded_feed_still_revokes_existing_intake(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d);bridge.synchronize(root,prod)
            path=root/'inbox'/p['content_id']/'package.json'
            write_json(root/'editorial_dispositions.json',{p['content_id']:{'status':'withdrawn','package_sha256':sha256(path),'reason':'No real application case'}})
            refresh(root)
            self.assertEqual(json.loads((root/'candidate_feed.json').read_text())['candidates'],[])
            with bridge.connection(prod/'queue.sqlite') as c:
                self.assertEqual(c.execute('SELECT state FROM material_intakes').fetchone()[0],'withdrawn')

    def test_failed_narrative_source_fidelity_blocks_request(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d)
            def review(prompt,data,**kw):
                answer,receipt=self.review(prompt,data,**kw)
                if 'fact_checks' not in prompt:answer['checks']['source_story_fidelity']=False
                return answer,receipt
            bridge.run(root,prod,1,reviewer=review,planner=self.plan)
            self.assertFalse((prod/'material_intake'/p['content_id']/'production_request.json').exists())

    def test_legacy_reassessment_does_not_replay_old_production(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d);bridge.synchronize(root,prod)
            bridge.set_state(prod/'queue.sqlite',p['content_id'],'held_story_review',{'reason':'v1 insufficient'})
            def source_review(prompt,data,**kw):
                self.assertEqual(set(data),{'sources'})
                answer,receipt=self.review('fact_checks',{'sources':data['sources'],'facts':p['facts']})
                return {'source_story':answer['source_story']},receipt
            reviewer=Mock(side_effect=source_review);producer=Mock();planner=Mock()
            result=bridge.run(root,prod,2,reviewer=reviewer,producer=producer,planner=planner)
            self.assertTrue(result['items'][0]['detail']['source_story_eligible'])
            bridge.run(root,prod,2,reviewer=reviewer,producer=producer,planner=planner)
            self.assertEqual(reviewer.call_count,1);producer.assert_not_called();planner.assert_not_called()

    def test_changed_package_and_expired_source_do_not_enter_review(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d)
            bridge.synchronize(root,prod)
            day=dt.date.fromisoformat(p['sources'][0]['published'])+dt.timedelta(days=7)
            result=bridge.synchronize(root,prod,today=day)
            self.assertEqual(result[0]['state'],'held_freshness')
            package=root/'inbox'/p['content_id']/'package.json';package.write_text(package.read_text()+' ')
            self.assertEqual(bridge.synchronize(root,prod)[0]['state'],'invalid_intake')

    def test_unchanged_feed_still_runs_enabled_bridge(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d)
            result=refresh(root)
            self.assertEqual(result['state'],'unchanged')
            self.assertEqual(result['production_bridge']['state'],'connected')
            with bridge.connection(prod/'queue.sqlite') as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM material_intakes').fetchone()[0],1)

    def authored(self,root,package):
        plan=film()
        for i,shot in enumerate(plan['shots']):
            shot.update(source_path=str(root/'asset.mp4'),source_start=i*8,
                        source_fit_policy='source_window',previsual_path=str(root/'frame.jpg'))
        path=root/'production_drafts'/package['content_id']/'plan.json'
        write_json(path,{'package_sha256':sha256(root/'inbox'/package['content_id']/'package.json'),
                         'author':'TEST_ONLY original draft','manifest':plan})
        return path

    def test_authored_asset_plan_freezes_draft_and_still_gets_real_gate(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d);path=self.authored(root,p)
            fallback=Mock(side_effect=AssertionError('draft must not reroll writer'))
            result=bridge.run(root,prod,1,reviewer=self.review,planner=fallback)
            fallback.assert_not_called();self.assertEqual(result['items'][0]['state'],'awaiting_storyboard')
            request=json.loads((prod/'material_intake'/p['content_id']/'production_request.json').read_text())
            manifest=json.loads(Path(request['manifest_path']).read_text());frozen=manifest['material_draft_provenance']
            path.write_text(path.read_text()+' ')
            self.assertEqual(sha256(frozen['path']),frozen['sha256'])
            require_review(request['manifest_path'],request['narrative_review_path'])

    def test_authored_plan_rejects_unbound_media_and_illegal_timecode(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d);path=self.authored(root,p)
            brief={'id':'test','facts':[{'id':'F1','text':'test','source_url':'https://cas.cn/a'}]}
            original=json.loads(path.read_text())
            for changes in ({'source_path':'/unbound.mp4'},{'source_start':100}):
                bad=copy.deepcopy(original);bad['manifest']['shots'][0].update(changes);write_json(path,bad)
                with self.assertRaises(ValueError):bridge.material_planner(brief,Path(d)/'output.json',p,root,Mock())

    def test_pending_scene_draft_requires_images_and_never_dispatches(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d);path=self.authored(root,p)
            draft=json.loads(path.read_text());draft['media_mode']='main_generated_scenes'
            for s in draft['manifest']['shots']:
                for key in ('source_path','source_start','source_fit_policy','previsual_path'):
                    s.pop(key,None)
            write_json(path,draft)
            with patch('workflow.batch.submit') as submit:
                result=bridge.run(root,prod,1,reviewer=self.review,planner=Mock())
                submit.assert_not_called()
            self.assertEqual(result['items'][0]['state'],'awaiting_storyboard')
            request=json.loads((prod/'material_intake'/p['content_id']/'production_request.json').read_text())
            self.assertTrue(request['missing']);self.assertFalse(request['production_ready'])
            require_review(request['manifest_path'],request['narrative_review_path'])

    def test_pending_scene_draft_rejects_claimed_assets(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d);path=self.authored(root,p)
            draft=json.loads(path.read_text());draft['media_mode']='main_generated_scenes'
            write_json(path,draft)
            with self.assertRaisesRegex(ValueError,'must not claim existing media'):
                bridge.material_planner({},Path(d)/'output.json',p,root,Mock())

    def test_resume_rejects_unknown_remote_outcome_before_any_call(self):
        with tempfile.TemporaryDirectory() as d:
            root,prod,p=self.fixture(d);bridge.synchronize(root,prod)
            bridge.set_state(prod/'queue.sqlite',p['content_id'],'material_attention',{'error':'unknown'})
            write_json(prod/'material_intake'/p['content_id']/'resume_audit.json',{
                'package_sha256':sha256(root/'inbox'/p['content_id']/'package.json'),
                'external_outcomes_known':False,'reason':'not known'})
            reviewer=Mock()
            with self.assertRaises(ValueError):bridge.run(root,prod,1,reviewer=reviewer,planner=self.plan,resume=[p['content_id']])
            reviewer.assert_not_called()


if __name__=='__main__':unittest.main()
