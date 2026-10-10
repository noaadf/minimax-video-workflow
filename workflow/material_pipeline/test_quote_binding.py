import unittest
from .quote_binding import bind_whitespace_quotes

def answer(q,verdict='pass',sid='S1'):
    return dict(verdict=verdict,source_story=dict(action=dict(evidence=[dict(source_id=sid,quote=q)])))

class QuoteBindingTests(unittest.TestCase):
    def test_nbsp_and_wrapping_rebind_to_actual_source(self):
        a=answer('Two frameworks: AutoSkill (2026) and Trace2Skill.')
        text='Two frameworks: AutoSkill\u00a0(2026) and\nTrace2Skill.'
        b,changes=bind_whitespace_quotes(a,{'S1':text})
        self.assertEqual(b['source_story']['action']['evidence'][0]['quote'],text)
        self.assertEqual(a['source_story']['action']['evidence'][0]['quote'],'Two frameworks: AutoSkill (2026) and Trace2Skill.')
        self.assertEqual(len(changes),1)
    def test_rejection_never_becomes_approval(self):
        b,_=bind_whitespace_quotes(answer('Nine correct tasks','revise'),{'S1':'Nine\u00a0correct tasks'})
        self.assertEqual(b['verdict'],'revise')
    def test_words_punctuation_and_source_are_not_repaired(self):
        for a,text in [(answer('Nine correct tasks'),'Eight correct tasks'),(answer('Nine correct tasks.'),'Nine correct tasks!'),(answer('Ninecorrect tasks'),'Nine correct tasks'),(answer('Nine correct tasks',sid='S2'),'Nine\u00a0correct tasks')]:
            with self.subTest(a=a):self.assertEqual(bind_whitespace_quotes(a,{'S1':text}),(a,[]))
    def test_ambiguous_source_byte_versions_are_not_selected(self):
        a=answer('Nine correct tasks')
        self.assertEqual(bind_whitespace_quotes(a,{'S1':'Nine\u00a0correct tasks; Nine\ncorrect tasks'}),(a,[]))
    def test_existing_exact_quote_is_unchanged(self):
        a=answer('Nine correct tasks');self.assertEqual(bind_whitespace_quotes(a,{'S1':'Nine correct tasks'}),(a,[]))

if __name__=='__main__':unittest.main()
