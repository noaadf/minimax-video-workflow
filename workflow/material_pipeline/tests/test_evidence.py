import copy
import json
import tempfile
import unittest
from pathlib import Path
from workflow.material_pipeline.core import content_id, enqueue_candidate, fact_id, sha256, validate


class EvidenceTest(unittest.TestCase):
    def fixture(self, root):
        body = '发布时间：2025-06-04\n试验完成120余次。'
        for name in ('source.html', 'source.txt'): (root/name).write_text(body)
        (root/'asset.mp4').write_bytes(b'fixture-media')
        (root/'frame.jpg').write_bytes(b'fixture-frame')
        (root/'transcript.json').write_text(json.dumps({'segments': [{'start': 0, 'end': 1, 'text': '试验'}]}))
        event = dict(entity='落塔', action='披露试验', date='2025-06-04', object='120余次')
        quote='试验完成120余次。';start=body.index(quote)
        fact=dict(id='F1',text=quote,subject='试验',predicate='完成次数',object='120余次',modality='reported',
                  verification='source_supported',evidence=[dict(source_id='S1',quote=quote,start=start,end=start+len(quote))])
        fact['fingerprint']=fact_id(fact)
        return dict(schema_version=1,content_id=content_id(event),title='落塔试验',event=event,
                    sources=[dict(id='S1',url='https://cas.cn/a',published='2025-06-04',date_evidence='发布时间：2025-06-04',
                                  archive_path='source.html',sha256=sha256(root/'source.html'),text_path='source.txt',text_sha256=sha256(root/'source.txt'))],
                    facts=[fact],assets=[dict(id='A1',source_id='S1',url='https://cas.cn/a.mp4',path='asset.mp4',
                                              sha256=sha256(root/'asset.mp4'),bytes=13,duration=2,use='production',rights=dict(status='unknown'))],
                    shots=[dict(id='S001',asset_id='A1',start=0,end=2,status='candidate',observation=None,
                                keyframes=[dict(at=1,path='frame.jpg',sha256=sha256(root/'frame.jpg'))])],
                    transcript=dict(status='automatic_unreviewed',asset_id='A1',path='transcript.json',sha256=sha256(root/'transcript.json')),
                    editorial={'beats':[{'fact_ids':['F1']}]},collection_pending=[],production_pending=[],production_asset_plan_reviewed=False)

    def test_hashes_and_spans_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);base=self.fixture(root);validate(base,root)
            for field,value in [('sha256','0'*64),('text_sha256','0'*64)]:
                bad=copy.deepcopy(base);bad['sources'][0][field]=value
                with self.assertRaises(ValueError):validate(bad,root)
            bad=copy.deepcopy(base);bad['facts'][0]['evidence'][0]['start']+=1
            with self.assertRaises(ValueError):validate(bad,root)

    def test_timecode_and_review_claims_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);base=self.fixture(root)
            for change in ({'end':3},{'start':float('nan')},{'status':'visually_reviewed'}):
                bad=copy.deepcopy(base);bad['shots'][0].update(change)
                with self.assertRaises(ValueError):validate(bad,root)

    def test_visible_date_formats_and_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for visible in ('2025/06/04', '2025.06.04', '2025年6月4日', 'Jun 4, 2025', 'June 04, 2025'):
                base=self.fixture(root)
                body=(root/'source.txt').read_text().replace('2025-06-04',visible)
                for name in ('source.txt','source.html'):(root/name).write_text(body)
                s=base['sources'][0]
                s.update(date_evidence=visible,sha256=sha256(root/'source.html'),text_sha256=sha256(root/'source.txt'))
                e=base['facts'][0]['evidence'][0];e['start']=body.index(e['quote']);e['end']=e['start']+len(e['quote'])
                validate(base,root)
                s['published']='2025-06-05'
                with self.assertRaises(ValueError):validate(base,root)

    def test_immutable_intake_and_rerun(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);package=self.fixture(root)
            first=enqueue_candidate(package,root)
            self.assertEqual(first,enqueue_candidate(package,root))
            bad=copy.deepcopy(package);bad['title']='同一个事实换标题'
            with self.assertRaises(ValueError):enqueue_candidate(bad,root)
            self.assertEqual(len(list((root/'inbox').glob('*/package.json'))),1)


if __name__ == '__main__':unittest.main()
