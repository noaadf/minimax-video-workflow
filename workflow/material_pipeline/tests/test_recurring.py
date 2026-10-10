import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow.material_pipeline.core import content_id, enqueue_candidate, write_json, sha256
from workflow.material_pipeline import recurring_collect as collector
from workflow.material_pipeline.refresh_feed import refresh
from workflow.material_pipeline.tests import test_evidence as evidence_helpers


class RecurringTest(unittest.TestCase):
    def test_editorial_withdrawal_stays_out_across_refreshes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);package=evidence_helpers.EvidenceTest().fixture(root)
            enqueue_candidate(package,root);refresh(root)
            path=root/'inbox'/package['content_id']/'package.json';original=path.read_bytes()
            write_json(root/'editorial_dispositions.json',{package['content_id']:{
                'status':'withdrawn','package_sha256':sha256(path),'reason':'User rejected editorial relevance'}})
            refresh(root);feed=json.loads((root/'candidate_feed.json').read_text())
            self.assertEqual(feed['candidates'],[])
            self.assertEqual(feed['withdrawn'][0]['content_id'],package['content_id'])
            self.assertEqual(refresh(root)['state'],'unchanged')
            self.assertEqual(path.read_bytes(),original)

    def test_withdrawal_cannot_hide_a_different_package(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);package=evidence_helpers.EvidenceTest().fixture(root)
            enqueue_candidate(package,root)
            write_json(root/'editorial_dispositions.json',{package['content_id']:{
                'status':'withdrawn','package_sha256':'0'*64,'reason':'Wrong version'}})
            with self.assertRaisesRegex(ValueError,'exact package'):refresh(root)

    def test_cann_entry_discovers_same_host_dated_article(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder=root/'source';folder.mkdir()
            url='https://cann.csdn.net/6abea225ad85947e68cd76ad.html'
            page={'archive_path':str(folder/'source.json'),'text':'昇腾CANN 2026-10-02','title':'CANN开发者社区','content_sha256':'1'*64,
                  'links':[url,url,'https://other.example/6abea225ad85947e68cd76ad.html','https://cann.csdn.net/']}
            with patch.object(collector,'fetch',return_value=page):
                result=collector.collect_page({'name':'昇腾CANN','url':'https://cann.csdn.net/'},root)
            self.assertEqual(result['article_urls'],[url])
            self.assertEqual((folder/'source.txt').read_text(),page['text'])

    def test_pending_backoff_preserves_and_resumes_article(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);config=root/'config.json';url='https://a.example/articles/retry'
            write_json(config,{'sources':[{'name':'a','url':'https://a.example/'}],'max_articles_per_run':1,'editorial_rules':{}})
            later=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).isoformat()
            write_json(root/'monitor/state.json',{'feeds':{},'seen_articles':{url:{'status':'pending','retry_after':later}}})
            def fake(entry,folder):
                return {'name':'a','url':entry['url'],'status':'archived','sha256':'1'*64,'archive':str(folder/'source.json'),
                        'article_urls':[url] if entry['url'].endswith('/') else []}
            with patch.object(collector,'ROOT',root),patch.object(collector,'collect_page',fake):
                report=json.loads(Path(collector.run(config)['report']).read_text())
                self.assertEqual(report['articles'],[])
                self.assertEqual(report['deferred_pending'][0]['url'],url)
                state=json.loads((root/'monitor/state.json').read_text())
                self.assertEqual(state['seen_articles'][url]['status'],'pending')
                state['seen_articles'][url]['retry_after']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=1)).isoformat()
                write_json(root/'monitor/state.json',state)
                report=json.loads(Path(collector.run(config)['report']).read_text())
                self.assertEqual([r['url'] for r in report['articles']],[url])
                self.assertEqual(json.loads((root/'monitor/state.json').read_text())['seen_articles'][url]['status'],'pending')

    def test_fair_discovery_and_pending_retry(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);config=root/'config.json'
            entries=[{'name':n,'url':f'https://{n}.example/'} for n in ('a','b','c')]
            write_json(config,{'sources':entries,'max_articles_per_run':4,'editorial_rules':{}})
            def fake(entry,folder):
                feed=entry['url'].endswith('/')
                return {'name':entry['name'],'url':entry['url'],'status':'archived','sha256':'1'*64,
                        'archive':str(folder/'source.json'),'article_urls':[f'https://{entry["name"]}.example/articles/{i}' for i in range(3)] if feed else []}
            with patch.object(collector,'ROOT',root),patch.object(collector,'collect_page',fake):
                first=collector.run(config)
                report=json.loads(Path(first['report']).read_text())
                self.assertEqual([r['name'] for r in report['articles']],['a','b','c','a'])
                state=json.loads((root/'monitor/state.json').read_text())
                excluded=report['articles'][0]['url'];state['seen_articles'][excluded]['status']='rejected'
                write_json(root/'monitor/state.json',state)
                second=collector.run(config);r=json.loads(Path(second['report']).read_text())
                self.assertNotIn(excluded,[a['url'] for a in r['articles']])
                self.assertIn(report['articles'][1]['url'],[a['url'] for a in r['articles']])
                self.assertTrue(all(not f['changed'] for f in r['feeds']))

    def test_cumulative_handoff_keeps_prior_package(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);one=evidence_helpers.EvidenceTest().fixture(root)
            enqueue_candidate(one,root);refresh(root)
            two=copy.deepcopy(one);two['event']['entity']='另一项试验';two['content_id']=content_id(two['event']);two['title']='另一项试验'
            enqueue_candidate(two,root);refresh(root)
            feed=json.loads((root/'candidate_feed.json').read_text())
            self.assertEqual({r['content_id'] for r in feed['candidates']},{one['content_id'],two['content_id']})
            before=(root/'candidate_feed.json').read_bytes()
            self.assertEqual(refresh(root)['state'],'unchanged')
            self.assertEqual((root/'candidate_feed.json').read_bytes(),before)


if __name__=='__main__':unittest.main()
