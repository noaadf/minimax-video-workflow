import copy,json,tempfile,unittest
from pathlib import Path
from workflow.revised_sources import validate_revised_sources
from workflow.ingest import parse
from workflow.plan import sha256,write_json
class RevisedSourceBinding(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name);self.quote='Factories adjust cargo compartments and fuel-tank capacity for overseas markets.'
  html=root/'source.html';html.write_text('<html><title>Report</title><p>'+self.quote+'</p></html>');source=parse(html.read_text(),'https://example.org/report');source.update(content_sha256=sha256(html),published='2026-10-01');write_json(root/'source.json',source)
  self.plan={'source_packet':{'archive':str(root/'source.json'),'source_url':source['url']},'facts':[{'id':'NEW','text':'New paraphrase','evidence_quote':self.quote,'source_url':source['url'],'source_sha256':source['content_sha256'],'source_published':source['published']}]};self.parent=copy.deepcopy(self.plan);self.parent['facts']=[]
 def test_new_fact_selection_from_original_article_allowed(self):self.assertEqual(validate_revised_sources(self.plan,self.parent)['validated_fact_ids'],['NEW'])
 def test_fake_quote_rejected(self):
  self.plan['facts'][0]['evidence_quote']='Invented factory produced a billion aircraft'
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
 def test_wrong_url_or_hash_rejected(self):
  self.plan['facts'][0]['source_url']='https://elsewhere.org'
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
 def test_changed_html_rejected(self):
  Path(self.plan['source_packet']['archive']).with_name('source.html').write_text('changed')
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
