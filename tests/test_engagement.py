import copy
import unittest
from workflow.engagement import CRITERIA, enrich, is_current


class EngagementTests(unittest.TestCase):
    def plan(self):
        return {'title': '深海取心', 'facts': [{'id': 'F1', 'text': '取得岩心'}],
                'narration': [{'text': '从深海带回一段岩心。'}]}

    def client(self, reject=False):
        calls = []
        def ask(prompt, data):
            calls.append(data)
            if 'Write three' in prompt:
                return {'candidates': [{'id': str(i), 'mechanism': m,
                         'question': '这段深海岩心，你最想知道它经历过什么？',
                         'connection_to_narration': '取回岩心'}
                        for i, m in enumerate(('experience', 'preference', 'curiosity'))]}, {'model': 'test'}
            return {'checks': [{'id': str(i), **{k: not reject for k in CRITERIA}}
                                for i in range(3)], 'selected_id': '0'}, {'model': 'test'}
        return ask, calls

    def test_selected_reviewed_question_and_cache(self):
        ask, calls = self.client(); p = enrich(self.plan(), ask)
        self.assertTrue(is_current(p)); self.assertEqual(len(calls), 2)
        enrich(p, ask); self.assertEqual(len(calls), 2)
        self.assertEqual(p['engagement']['selected_question'], p['comment_prompt'])

    def test_changed_narration_or_question_invalidates_review(self):
        ask, _ = self.client(); p = enrich(self.plan(), ask)
        for field in ('narration', 'comment_prompt'):
            changed = copy.deepcopy(p)
            changed[field] = [{'text': '不同内容'}] if field == 'narration' else '你怎么看'
            self.assertFalse(is_current(changed))

    def test_failed_editorial_check_cannot_select_question(self):
        ask, calls = self.client(reject=True)
        with self.assertRaises(ValueError): enrich(self.plan(), ask)
        self.assertEqual(len(calls), 4)

    def test_missing_candidate_review_is_not_a_pass(self):
        ask, _ = self.client()
        def incomplete(prompt, data):
            result, receipt = ask(prompt, data)
            if 'checks' in result: result['checks'].pop()
            return result, receipt
        with self.assertRaises(ValueError): enrich(self.plan(), incomplete)

    def test_numeric_candidates_and_string_selection(self):
        ask, calls = self.client()
        def mixed(prompt, data):
            result, receipt = ask(prompt, data)
            for row in result.get('candidates', result.get('checks', [])):
                row['id'] = int(row['id'])
            return result, receipt
        p = enrich(self.plan(), mixed)
        self.assertTrue(is_current(p))
        self.assertEqual(len(calls), 2)

    def test_throughput_cosmetic_failure_retains_original_without_new_selection(self):
        ask, calls = self.client()
        def cosmetic(prompt, data):
            result, receipt = ask(prompt, data)
            for row in result.get('checks', []): row['easy_to_answer'] = False
            return result, receipt
        p = self.plan()
        p.update(quality_profile='throughput_v1', comment_prompt='你想用岩心研究什么问题？')
        enrich(p, cosmetic)
        self.assertEqual(p['engagement']['status'], 'advisory')
        self.assertEqual(p['comment_prompt'], '你想用岩心研究什么问题？')
        self.assertTrue(is_current(p))
        enrich(p, cosmetic)
        self.assertEqual(len(calls), 4)

    def test_throughput_does_not_waive_unsupported_premise(self):
        ask, _ = self.client(reject=True)
        p = self.plan()
        p.update(quality_profile='throughput_v1', comment_prompt='你想用岩心研究什么问题？')
        with self.assertRaises(ValueError): enrich(p, ask)
