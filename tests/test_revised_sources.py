import copy,json,tempfile,unittest
from pathlib import Path
from workflow.ingest import parse
from workflow.plan import write_json,sha256
from workflow.revised_sources import validate_revised_sources

class SupplementalArchives(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.url='https://english.news.cn/report';h=self.root/'source.html';h.write_text('<title>Original</title><p>Original mission first observations were released today.</p>');r=parse(h.read_text(),self.url);r.update(content_sha256=sha256(h),published='2026-09-30');write_json(self.root/'source.json',r)
  self.parent={'source_packet':{'archive':str(self.root/'source.json'),'source_url':self.url,'published':'2026-09-30'}}
  self.plan=copy.deepcopy(self.parent);self.quote='The hot supernova remnant is a bright test target for the instrument.'
  self.h=self.root/'esa.html';self.h.write_text('<title>ESA test image</title><h1>ESA test image</h1><p>30/09/2026</p><p>'+self.quote+'</p>')
  self.url2='https://www.esa.int/ESA_Multimedia/Images/test';self.record=parse(self.h.read_text(),self.url2);self.record.update(schema_version=1,original_source_url=self.url,html_path=str(self.h),content_sha256=sha256(self.h),published='2026-09-30');self.r=self.root/'esa.json';self.rebind()
  self.plan['facts']=[{'id':'F1','evidence_quote':self.quote,'source_url':self.url2,'source_sha256':sha256(self.h),'source_published':'2026-09-30'}]
 def rebind(self):
  write_json(self.r,self.record);self.plan['supplemental_sources']=[{'path':str(self.r),'sha256':sha256(self.r)}]
 def test_verified_supplement_keeps_original_identity(self):
  self.assertEqual(validate_revised_sources(self.plan,self.parent)['validated_fact_ids'],['F1']);self.assertEqual(self.plan['source_packet'],self.parent['source_packet'])
 def test_original_identity_cannot_be_replaced(self):
  self.plan['source_packet']['source_url']=self.url2
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
 def test_unregistered_fact_source_rejected(self):
  self.plan['supplemental_sources']=[]
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
 def test_user_provenance_fact_skips_archive_binding(self):
  # Owner-authorized editorial fact (user channel): no archive entry required,
  # but it still counts for unique IDs. F1 keeps its full archive binding.
  self.plan['facts'].append({'id':'F5','provenance':'user','text':'用户授权创作事实',
   'source_url':'user://editorial-directive/2026-10-05','evidence_quote':'用户口头指令'})
  result=validate_revised_sources(self.plan,self.parent)
  self.assertIn('F1',result['validated_fact_ids'])   # source fact fully bound
  self.assertIn('F5',result['validated_fact_ids'])   # user fact accepted as-is
  self.plan['facts'].append({'id':'F5','provenance':'user','text':'duplicate id'})
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
 def test_html_tamper_rejected(self):
  self.h.write_text(self.h.read_text()+'changed')
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
 def test_record_tamper_rejected(self):
  self.r.write_text(self.r.read_text()+' ')
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
 def test_rehashed_fabricated_text_rejected(self):
  self.record['text']+='\nInvented proof';self.rebind()
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
 def test_quote_must_be_exact(self):
  self.plan['facts'][0]['evidence_quote']='The mission has achieved all possible scientific goals.'
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
 def test_record_date_not_url_or_body_inference(self):
  self.record['published']='2026-09-29';self.rebind()
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
 def test_fact_date_and_sha_bound(self):
  for key,value in [('source_published','2026-09-29'),('source_sha256','a'*64)]:
   with self.subTest(key=key):
    p=copy.deepcopy(self.plan);p['facts'][0][key]=value
    with self.assertRaises(ValueError):validate_revised_sources(p,self.parent)
 def test_arbitrary_or_spoofed_domain_rejected(self):
  for url in ['https://www.esa.int.evil.test/a','https://example.org/a','http://www.esa.int/a']:
   self.record['url']=url;self.rebind();self.plan['facts'][0]['source_url']=url
   with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
 def test_archive_must_name_same_original_article(self):
  self.record['original_source_url']='https://english.news.cn/another';self.rebind()
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)
 def test_duplicate_sources_rejected(self):
  self.plan['supplemental_sources']*=2
  with self.assertRaises(ValueError):validate_revised_sources(self.plan,self.parent)

if __name__=='__main__':unittest.main()
