import unittest
from workflow.delivery_policy import assess
class PolicyTests(unittest.TestCase):
    def test_story_action_gate_is_advisory_after_owner_relaxation(self):
        # Owner directive 2026-10-06: shot-review evidence no longer blocks delivery.
        q=self.quality('visual_repetition','low');q['storyboard_required']=True
        for status in (None,'review_required','failed'):
            q['shot_review']={'status':status}
            result=assess(q)
            self.assertTrue(result['ready_for_delivery'])
            self.assertIn('story_action_unverified',[f['category'] for f in result['advisories']])
        q['shot_review']={'status':'sampled_checks_passed'}
        self.assertTrue(assess(q)['ready_for_delivery'])
    def test_unavailable_asr_is_not_homophone_uncertainty(self):
        q=self.quality('visual_repetition','low')
        for status in ('speech_not_assessed','speech_deferred','missing_transcript'):
            q['speech']={'status':status,'review_required':True}
            self.assertFalse(assess(q)['ready_for_delivery'])
        for status in ('homophone_spelling_only','phonetic_review_required'):
            q['speech']={'status':status,'review_required':True}
            self.assertTrue(assess(q)['ready_for_delivery'])
    def quality(self,category,severity):return {'passed':True,'editorial':{'receipt':{'model':'tested'},'review':{'verdict':'review','findings':[{'category':category,'severity':severity,'issue':'example'}]}}}
    def test_cosmetic_issues_do_not_hold_delivery(self):
        for cat in ['visual_repetition','subtitle','visual_quality']:
            self.assertTrue(assess(self.quality(cat,'medium'))['ready_for_delivery'])
    def test_material_truth_and_geometry_still_block(self):
        for cat in ['geometry','fact']:
            self.assertFalse(assess(self.quality(cat,'medium'))['ready_for_delivery'])
    def test_no_review_or_broken_media_not_released(self):
        # Owner-relaxed 2026-10-06: missing review evidence is advisory; broken media still blocks.
        result=assess({'passed':True})
        self.assertTrue(result['ready_for_delivery'])
        self.assertIn('review_unavailable',[f['category'] for f in result['advisories']])
        q=self.quality('visual_repetition','low');q['passed']=False
        self.assertFalse(assess(q)['ready_for_delivery'])
