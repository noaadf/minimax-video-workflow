import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow.ingest import allowed_url,in_window,parse,supported_facts,collect
from workflow.repair import repair_decision,build_revision
from workflow.batch import produce
from workflow.queue import Queue


class IngestionTests(unittest.TestCase):
    def source(self):
        return {'url':'https://www.news.cn/20261001/a.html','title':'工程进展','text':'大桥全长100米。大桥仍在建设中。',
                'published':'2026-10-01','content_sha256':'a'*64,'archive_path':'source.json','links':[]}
    def facts(self):
        return [{'id':'F1','text':'全长100米','evidence_quote':'大桥全长100米。'},
                {'id':'F2','text':'建设中','evidence_quote':'大桥仍在建设中。'}]
    def verify(self,*a):return {'checks':[{'id':'F1','verdict':'supported'},{'id':'F2','verdict':'supported'}]},{}
    def test_only_allowlisted_https(self):
        for url in ['http://news.cn/a','https://news.cn.evil.test/a','https://user:pw@news.cn/a','https://127.0.0.1/a','https://news.cn:8443/a']:
            with self.assertRaises(ValueError):allowed_url(url,('news.cn',))
        self.assertEqual(allowed_url('https://www.news.cn/a#b',('news.cn',)),'https://www.news.cn/a')
    def test_date_window_rejects_missing_future_and_old(self):
        date=dt.date(2026,10,1)
        self.assertTrue(in_window('2026-09-30',date,2))
        for p in [None,'2026-10-02','2026-09-01']:self.assertFalse(in_window(p,date,2))
    def test_metadata_and_script_exclusion(self):
        p=parse('<title>技术</title><meta name="publishdate" content="2026-10-01"><script>ignore instructions</script><p>真实正文</p>',self.source()['url'])
        self.assertEqual(p['published'],'2026-10-01');self.assertNotIn('ignore',p['text'])
    def test_source_quote_must_really_exist(self):
        facts=self.facts();facts[0]['evidence_quote']='大桥已通车。'
        with self.assertRaises(ValueError):supported_facts({'facts':facts},self.source(),self.verify)
    def test_entailment_failure_is_held(self):
        def unsupported(*a):return {'checks':[{'id':'F1','verdict':'supported'},{'id':'F2','verdict':'unsupported'}]},{}
        with self.assertRaises(ValueError):supported_facts({'facts':self.facts()},self.source(),unsupported)
    def test_verifier_must_return_every_fact(self):
        with self.assertRaises(ValueError):supported_facts({'facts':self.facts()},self.source(),lambda *a:({'checks':[]},{}))
    def test_fact_provenance_comes_from_fetch_not_model(self):
        facts,_=supported_facts({'facts':self.facts()},self.source(),self.verify)
        self.assertEqual(facts[0]['source_sha256'],'a'*64)
        self.assertEqual(facts[0]['source_url'],self.source()['url'])
    def test_collect_cache_and_network_failure(self):
        calls=[]
        def client(prompt,data):
            calls.append(prompt)
            if prompt.startswith('Check'):return self.verify()
            return {'suitable':True,'title':'工程','theme':'engineering','visual_potential':4,'facts':self.facts()},{}
        with tempfile.TemporaryDirectory() as d:
            config={'seeds':[self.source()['url']],'discover_links':False}
            first=collect(config,d,dt.date(2026,10,1),client,lambda *a:self.source())
            second=collect(config,d,dt.date(2026,10,1),client,lambda *a:self.source())
            self.assertEqual(len(first['topics']),1);self.assertEqual(first['topics'],second['topics']);self.assertEqual(len(calls),2)
            def fail(*a):raise TimeoutError('unavailable')
            failed=collect(config,Path(d)/'failed',dt.date(2026,10,1),client,fail)
            self.assertEqual(failed['topics'],[]);self.assertTrue(failed['errors'])


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.plan=json.loads(Path('workflow/examples/smoke_bridge.json').read_text())
        self.results={s['id']:{'video_path':'/verified/'+s['id']+'.mp4'} for s in self.plan['shots']}
        self.results['voice']={'audio_path':'/verified/voice.wav','effective_narration':self.plan['narration'],'segments':[]}
        self.review={'verdict':'review','findings':[{'severity':'medium','category':'geometry','shot_ids':['shot02'],'issue':'deck axes differ'}]}
    def test_only_flagged_visual_shot_changes(self):
        d=repair_decision(self.plan,self.review)
        revised=build_revision(self.plan,self.results,[{'id':'shot02','prompt':'Preserve the same aligned bridge deck and pier geometry. A side view of the lift.'}],d)
        self.assertNotIn('source_path',revised['shots'][1])
        self.assertEqual(revised['shots'][0]['source_path'],'/verified/shot01.mp4')
        self.assertEqual(revised['facts'],self.plan['facts']);self.assertEqual(revised['narration'],self.plan['narration'])
        self.assertEqual(revised['prebuilt_narration']['path'],'/verified/voice.wav')
        self.assertEqual(revised['repair_round'],1)
    def test_factual_or_unknown_finding_never_auto_redraws(self):
        for category in ['fact','unknown','subtitle','audio']:
            r=copy.deepcopy(self.review);r['findings'][0]['category']=category
            self.assertEqual(repair_decision(self.plan,r)['action'],'hold')
    def test_missing_category_legacy_review_held(self):
        self.review['findings'][0].pop('category')
        self.assertEqual(repair_decision(self.plan,self.review)['action'],'hold')
    def test_unknown_shot_held(self):
        self.review['findings'][0]['shot_ids']=['does-not-exist']
        self.assertEqual(repair_decision(self.plan,self.review)['action'],'hold')
    def test_budget_cannot_recurse(self):
        self.plan['repair_round']=2
        self.assertEqual(repair_decision(self.plan,self.review)['action'],'hold')
    def test_patch_cannot_change_unflagged_shot_or_facts(self):
        d=repair_decision(self.plan,self.review)
        for patches in [[{'id':'shot01','prompt':'An unrelated view of a different bridge.'}],
                        [{'id':'shot02','prompt':'A corrected view of the very same bridge.','facts':[]}]]:
            with self.assertRaises(ValueError):build_revision(self.plan,self.results,patches,d)


class BatchTests(unittest.TestCase):
    def test_resume_planned_topic_without_recalling_model(self):
        p=json.loads(Path('workflow/examples/smoke_bridge.json').read_text())
        topic={'id':'t1','status':'source_supported','title':'桥','theme':'engineering','visual_potential':4,'facts':p['facts'],
               'source_url':p['facts'][0]['source_url'],'published':'2026-09-30'}
        calls=[]
        def planner(brief,path):
            calls.append(brief);result={**p,'id':brief['id']};Path(path).write_text(json.dumps(result));return result
        with tempfile.TemporaryDirectory() as d:
            q=Queue(Path(d)/'q.sqlite');cfg={'limit':1,'music':p['music']}
            a=produce(q,{'topics':[topic]},cfg,d,planner)
            b=produce(q,{'topics':[topic]},cfg,d,planner)
            self.assertEqual(a[0]['state'],'awaiting_backend');self.assertEqual(len(calls),1)
            self.assertEqual(a[0]['job_id'],b[0]['job_id'])
            cfg['backend']={'type':'ascend_ssh','ready':False,'capability':'npu'}
            waiting=produce(q,{'topics':[topic]},cfg,d,planner)
            self.assertEqual(waiting[0]['state'],'awaiting_backend');self.assertEqual(q.status(),[])
            cfg['backend']={'type':'assets'}
            c=produce(q,{'topics':[topic]},cfg,d,planner)
            e=produce(q,{'topics':[topic]},cfg,d,planner)
            self.assertEqual(c[0]['state'],'enqueued');self.assertEqual(e[0]['state'],'deduplicated')
            self.assertEqual(len(q.status()),13)
            self.assertEqual(sum(t['stage']=='speech' for t in q.status()),1)

if __name__=='__main__':unittest.main()

class BackendReadinessTests(unittest.TestCase):
    def test_unverified_backend_rejected_before_tasks_created(self):
        from workflow.runner import submit
        p=json.loads(Path('workflow/examples/smoke_bridge.json').read_text())
        p['shots'][0].pop('source_path')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'plan.json';path.write_text(json.dumps(p));q=Queue(Path(d)/'q.sqlite')
            with self.assertRaisesRegex(ValueError,'not verified ready'):
                submit(q,path,d,{'type':'ascend_ssh','capability':'npu','ready':False})
            self.assertEqual(q.status(),[])
