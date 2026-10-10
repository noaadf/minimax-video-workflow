import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from workflow.ingest import parse
from workflow.material_pipeline import core
from workflow.material_pipeline.publication_index import validate


class PublicationIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.card = '<article><span>2026-10-08</span>\n<h3><a href="/blog/example">Scientific reproduction</a></h3></article>'
        other = '<article><span>2026-09-28</span><h3><a href="/blog/other">Other research</a></h3></article>'
        self.raw = '<html><body>' + self.card + other + '</body></html>'
        (self.root/'original.html').write_text('<html><body>Undated original experiment and results.</body></html>')
        (self.root/'original.txt').write_text(parse((self.root/'original.html').read_text(), 'https://publisher.test/blog/example')['text'])
        self.source = dict(id='S1', url='https://publisher.test/blog/example', title='Scientific reproduction',
            published='2026-10-08', date_evidence='2026-10-08\nScientific reproduction',
            archive_path='original.html', sha256=core.sha256(self.root/'original.html'),
            text_path='original.txt', text_sha256=core.sha256(self.root/'original.txt'))
        self.index(self.raw, self.card)

    def index(self, raw, card):
        (self.root/'index.html').write_text(raw)
        (self.root/'index.txt').write_text(parse(raw, 'https://www.publisher.test/blog')['text'])
        start = raw.index(card)
        self.source['publication_index'] = dict(kind='publisher_article_card', url='https://www.publisher.test/blog',
            archive_path='index.html', sha256=core.sha256(self.root/'index.html'), text_path='index.txt',
            text_sha256=core.sha256(self.root/'index.txt'), entry_start=start, entry_end=start+len(card))

    def test_date_bound_to_original_publisher_card_body_unchanged(self):
        before = (self.root/'original.txt').read_bytes()
        self.assertEqual(validate(self.source, self.root)['published'], '2026-10-08')
        core._validate_sources({'sources':[self.source]}, self.root)
        self.assertEqual((self.root/'original.txt').read_bytes(), before)
        self.assertNotIn(b'2026-10-08', before)

    def test_old_source_without_index_still_requires_body_date(self):
        self.source.pop('publication_index')
        with self.assertRaisesRegex(ValueError, 'publication date'):
            core._validate_sources({'sources':[self.source]}, self.root)

    def test_wrong_metadata_rejected(self):
        original = copy.deepcopy(self.source)
        for field, value in [('published','2026-09-28'), ('title','Other research'),
                             ('url','https://publisher.test/blog/other'), ('url','https://foreign.test/blog/example'),
                             ('date_evidence','2026-10-08')]:
            with self.subTest(field=field,value=value):
                self.source = copy.deepcopy(original); self.source[field] = value
                with self.assertRaises(ValueError): validate(self.source, self.root)

    def test_hash_and_parsed_text_rejected(self):
        for name in ('index.html','index.txt'):
            with self.subTest(name=name):
                self.index(self.raw,self.card)
                (self.root/name).write_text('forged')
                with self.assertRaises(ValueError): validate(self.source,self.root)
        self.index(self.raw,self.card)
        (self.root/'index.txt').write_text('forged')
        self.source['publication_index']['text_sha256'] = core.sha256(self.root/'index.txt')
        with self.assertRaisesRegex(ValueError,'differs'): validate(self.source,self.root)

    def test_invalid_spans_rejected(self):
        original = copy.deepcopy(self.source)
        for start, end in [(True,111),(-1,111),(0,99999),(12,12),(12,len(self.raw)-14)]:
            with self.subTest(start=start,end=end):
                self.source = copy.deepcopy(original)
                self.source['publication_index'].update(entry_start=start,entry_end=end)
                with self.assertRaises(ValueError): validate(self.source,self.root)

    def test_inert_article_strings_not_date_proof(self):
        for wrapper in ['<script>{}</script>', '<style>{}</style>', '<!--{}-->',
                        '<noscript>{}</noscript>', '<template>{}</template>']:
            with self.subTest(wrapper=wrapper):
                raw = '<html><body>' + wrapper.format(self.card) + self.card + '</body></html>'
                self.index(raw,self.card)
                with self.assertRaisesRegex(ValueError,'original article element'): validate(self.source,self.root)

    def test_adjacent_card_date_not_reused(self):
        card = self.card.replace('2026-10-08','2026-09-28')
        self.index(self.raw.replace(self.card,card),card)
        with self.assertRaisesRegex(ValueError,'publication date'): validate(self.source,self.root)

    def test_escaping_proof_path_rejected(self):
        self.source['publication_index']['archive_path'] = '../index.html'
        with self.assertRaises(ValueError): validate(self.source,self.root)

    def test_production_adapter_keeps_facts_and_date_provenance_separate(self):
        from workflow.material_pipeline import production_bridge, story_gate
        original = (self.root/'original.html').read_bytes()
        package = dict(content_id='material_example', title='Research example',
            event={'entity':'research team'}, sources=[self.source],
            editorial={'cautions':['reported result']},
            facts=[dict(id='F1', text='The original describes an experiment and results.',
                modality='reported', evidence=[{'source_id':'S1','quote':'Undated original experiment and results.'},
                    {'source_id':'S1','quote':'experiment and results.'}])])
        topic, packet = production_bridge.production_topic(package,self.root,self.root/'intake')
        archive = json.loads(Path(packet['archive']).read_text())
        self.assertEqual(archive['text'], (self.root/'original.txt').read_text())
        self.assertEqual((self.root/'intake/source/original_source.raw').read_bytes(), original)
        self.assertEqual(archive['publication_evidence_path'],str((self.root/'index.txt').resolve()))
        self.assertEqual(archive['publication_index_evidence']['published'],'2026-10-08')
        self.assertEqual(topic['facts'][0]['evidence_quote'],'Undated original experiment and results.')
        self.assertEqual(topic['facts'][0]['evidence_quotes'],['Undated original experiment and results.','experiment and results.'])
        self.assertEqual([x['evidence_quote'] for x in topic['facts'][0]['evidence_bindings']],topic['facts'][0]['evidence_quotes'])
        docs = story_gate.source_documents(package,self.root)
        self.assertEqual(docs[0]['text'], archive['text'])
        self.assertEqual(docs[0]['publication_index_evidence']['published'],'2026-10-08')

    def test_extra_fact_quote_must_bind_to_same_archived_body(self):
        from workflow.material_pipeline import production_bridge
        for row in [{'source_id':'S1','quote':'Fabricated secondary evidence'},
                    {'source_id':'S2','quote':'experiment and results.'}]:
            with self.subTest(row=row):
                package = dict(content_id='material_example',title='Research example',event={},
                    sources=[self.source],editorial={},facts=[dict(id='F1',text='experiment',modality='reported',
                    evidence=[{'source_id':'S1','quote':'Undated original experiment and results.'},row])])
                with self.assertRaises(ValueError):
                    production_bridge.production_topic(package,self.root,self.root/'intake')


if __name__ == '__main__': unittest.main()
