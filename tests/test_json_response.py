import unittest
from workflow.json_response import parse_object
class ResponseTests(unittest.TestCase):
 def test_bare_keys_and_mixed(self):
  value,changed=parse_object('{"verdict":"review",findings:[{severity:"low",issue:"literal {key: untouched} and \\\"quote\\\""}],confidence:0.8}')
  self.assertTrue(changed);self.assertEqual(value['findings'][0]['issue'],'literal {key: untouched} and "quote"')
 def test_normal_and_fenced(self):
  self.assertEqual(parse_object('```json\n{"a":1}\n```'),({'a':1},False))
 def test_bad_values_never_repaired(self):
  for text in ('{a:undefined}','{a:1,}','{a:"unterminated}','{a:1,a:2}','{a:NaN}','prefix {a:1}','{a:1} trailing','{a:\'x\'}','[{a:1}]'):
   with self.subTest(text=text),self.assertRaises(ValueError):parse_object(text)

class ReviewSummaryTests(unittest.TestCase):
 def test_preserves_verdict_and_findings(self):
  from workflow.json_response import parse_review_with_summary
  import json
  for verdict in ('pass','review','reject'):
   value={'verdict':verdict,'confidence':0.9,'findings':[],'not_assessed':['audio']}
   self.assertEqual(parse_review_with_summary(json.dumps(value)+'\n**Assessment summary:** Commentary.'),value)
 def test_rejects_truncation_ambiguity_and_invalid_schema(self):
  from workflow.json_response import parse_review_with_summary
  import json
  base=json.dumps({'verdict':'pass','confidence':0.9,'findings':[],'not_assessed':[]})
  for text in (base+' trailing',base+'\n**Assessment summary:** {}',base+'\n**Assessment summary:** []',base[:-1]+'\n**Assessment summary:** Done',base.replace('0.9','2')+'\n**Assessment summary:** Done',base.replace('"confidence": 0.9','"confidence": 0.9, "confidence": 0.1')+'\n**Assessment summary:** Done'):
   with self.subTest(text=text),self.assertRaises(ValueError):parse_review_with_summary(text)
