import unittest
from unittest.mock import patch
from workflow.writing import shorten

class WritingValidationTests(unittest.TestCase):
    def run_short(self, answers):
        plan={'facts':[], 'narration':[{'text':'这是一段需要改短的内容。'},{'text':'这是另外一段需要改短的内容。'}]}
        measurements=[{'index':i,'source_seconds':8,'slot_seconds':4} for i in range(2)]
        with patch('workflow.writing.ask_json',side_effect=[(x,{}) for x in answers]) as call:
            result=shorten(plan,measurements)
            return result,call.call_args_list
    def test_string_integer_indices_normalize(self):
        (rows,_),_=self.run_short([{'revisions':[{'index':'0','text':'向前。'},{'index':1,'text':'出发。'}]}])
        self.assertEqual([r['index'] for r in rows],[0,1])
    def test_missing_line_feedback_requires_complete_list(self):
        (_, _),calls=self.run_short([{'revisions':[{'index':1,'text':'出发。'}]}, {'revisions':[{'index':0,'text':'向前。'},{'index':1,'text':'出发。'}]}])
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[1].args[1]['feedback']['required_indices'],[0,1])
    def test_duplicate_wrong_bool_float_indices_never_accepted(self):
        for ids in ([0,0],[0,2],[False,1],[0.0,1]):
            with self.subTest(ids=ids),self.assertRaisesRegex(ValueError,'three validation'):
                self.run_short([{'revisions':[{'index':i,'text':'向前。'} for i in ids]}]*3)
    def test_missing_schema_and_non_string_text_bounded(self):
        for value in ({},{'revisions':None},{'revisions':[{'index':0,'text':None},{'index':1,'text':12}]}):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'three validation'):
                self.run_short([value]*3)
