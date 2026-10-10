import unittest
from workflow.speech_qc import compare, normalize


class SpeechTests(unittest.TestCase):
    def test_polyphone_ambiguity_remains_review_required(self):
        from workflow.speech_gate import route
        report = {'expected': '一道工序常要学两三个月',
                  'recognized': '一道工序长要学两三个月', 'binding': {}}
        result = route(report, 'receipt.json', True, 'digest')
        difference = result['comparison']['differences'][0]
        self.assertEqual(difference['classification'], 'polyphone_ambiguity')
        self.assertIn('chang2', difference['recognized_reading_candidates'][0])
        self.assertTrue(result['review_required'])
        self.assertFalse(result['content_approved'])
        self.assertEqual(result['status'], 'phonetic_review_required')

    def test_numeric_notation_and_punctuation_equivalence(self):
        self.assertEqual(normalize('二〇二五年，六百二十五米，一千四百二十米。'),normalize('2025年625米1420米'))
        self.assertEqual(normalize('一亿摄氏度，十点二英寸'),normalize('1亿摄氏度10.2英寸'))
        self.assertEqual(normalize('橋面離水面625米。'),normalize('桥面离水面六百二十五米.'))
    def test_missing_negation_and_wrong_number_are_not_normalized_away(self):
        self.assertNotEqual(compare('不是商业发电','是商业发电')['status'],'transcript_match')
        self.assertNotEqual(compare('六百二十五米','六十五米')['status'],'transcript_match')
    def test_homophone_is_not_automatically_excused(self):
        self.assertTrue(compare('钢桁梁','钢横梁')['differences'])
    def test_missing_entire_narration_fails(self):
        r=compare('中国建设者','')
        self.assertEqual(r['character_error_rate'],1)
        self.assertFalse(r['content_approved'])

    def test_homophone_routing_retains_tone_changes(self):
        from workflow.speech_qc import classify_phonetics
        self.assertEqual(classify_phonetics('钢桁梁','刚横梁')['differences'][0]['classification'],'homophone_spelling')
        self.assertEqual(classify_phonetics('主缆','竹篮')['differences'][0]['classification'],'phonetic_difference')
        self.assertEqual(classify_phonetics('亿度高温','异度高温')['differences'][0]['classification'],'homophone_spelling')
        self.assertEqual(classify_phonetics('一亿度','异度')['differences'][0]['classification'],'phonetic_difference')
