import copy
import unittest

from workflow.delivery_policy import assess


class DeliveryReviewSchemaTests(unittest.TestCase):
    def quality(self):
        return {'passed': True, 'editorial': {'receipt': {'model': 'synthetic'},
                'review': {'verdict': 'pass', 'findings': []}}}

    def assert_invalid(self, quality):
        # Owner-relaxed 2026-10-06: malformed review evidence is advisory, not blocking.
        original = copy.deepcopy(quality)
        result = assess(quality)
        self.assertTrue(result['ready_for_delivery'])
        self.assertIn('invalid_review', [x['category'] for x in result['advisories']])
        self.assertEqual(quality, original)

    def test_missing_or_unknown_verdict_is_not_a_completed_review(self):
        for verdict in (None, '', 'accepted', True, []):
            with self.subTest(verdict=verdict):
                quality = self.quality()
                quality['editorial']['review']['verdict'] = verdict
                self.assert_invalid(quality)
        quality = self.quality()
        quality['editorial']['review'] = {}
        self.assert_invalid(quality)

    def test_findings_must_be_present_as_a_list(self):
        quality = self.quality()
        del quality['editorial']['review']['findings']
        self.assert_invalid(quality)
        for findings in (None, {}, '', 0):
            with self.subTest(findings=findings):
                quality = self.quality()
                quality['editorial']['review']['findings'] = findings
                self.assert_invalid(quality)

    def test_malformed_finding_blocks_instead_of_crashing_or_becoming_advisory(self):
        for finding in (None, 'bad', {},
                        {'category': 'geometry', 'severity': 'critical', 'issue': 'Bad geometry'},
                        {'category': 'typo', 'severity': 'high', 'issue': 'Unclassified defect'},
                        {'category': [], 'severity': 'high', 'issue': 'Bad category'},
                        {'category': 'fact', 'severity': 'high', 'issue': ''},
                        {'category': 'fact', 'severity': 'high', 'issue': None}):
            with self.subTest(finding=finding):
                quality = self.quality()
                quality['editorial']['review']['findings'] = [finding]
                self.assert_invalid(quality)

    def test_valid_polish_and_speech_findings_keep_existing_policy(self):
        for category in ('visual_repetition', 'visual_quality', 'subtitle', 'speech', 'engagement'):
            with self.subTest(category=category):
                quality = self.quality()
                finding = {'category': category, 'severity': 'medium', 'issue': 'Synthetic advisory'}
                quality['editorial']['review'] = {'verdict': 'review', 'findings': [finding]}
                result = assess(quality)
                self.assertTrue(result['ready_for_delivery'])
                self.assertEqual(result['advisories'], [finding])

    def test_valid_material_findings_still_block_and_bare_reject_is_advisory(self):
        for category in ('geometry', 'fact', 'unknown'):
            quality = self.quality()
            finding = {'category': category, 'severity': 'high', 'issue': 'Synthetic defect'}
            quality['editorial']['review']['findings'] = [finding]
            self.assertIn(finding, assess(quality)['blockers'])
        # Owner-relaxed 2026-10-06: reject without a material finding no longer blocks.
        quality = self.quality()
        quality['editorial']['review']['verdict'] = 'reject'
        self.assertTrue(assess(quality)['ready_for_delivery'])

    def test_valid_empty_pass_is_allowed(self):
        self.assertTrue(assess(self.quality())['ready_for_delivery'])

    def test_missing_or_non_object_editorial_is_unavailable(self):
        # Owner-relaxed 2026-10-06: missing editorial evidence is advisory.
        for editorial in (None, [], 'incomplete', {}):
            with self.subTest(editorial=editorial):
                quality = self.quality()
                quality['editorial'] = editorial
                result = assess(quality)
                self.assertTrue(result['ready_for_delivery'])
                self.assertIn('review_unavailable', [x['category'] for x in result['advisories']])

    def test_unknown_or_malformed_speech_record_blocks(self):
        for speech in (None, [], '', {}, {'status': 'unexpected_error', 'review_required': False},
                       {'status': 'failed'}, {'status': None}, {'status': []}):
            with self.subTest(speech=speech):
                quality = self.quality()
                quality['speech'] = speech
                result = assess(quality)
                self.assertFalse(result['ready_for_delivery'])
                self.assertIn('audio_check_unavailable', [x['category'] for x in result['blockers']])

    def test_recognized_speech_states_keep_existing_routing(self):
        for status, allowed in [('transcript_match', True), ('homophone_spelling_only', True),
                                ('phonetic_review_required', True), ('missing_transcript', False),
                                ('speech_not_assessed', False), ('speech_deferred', False)]:
            with self.subTest(status=status):
                quality = self.quality()
                quality['speech'] = {'status': status, 'review_required': status != 'transcript_match'}
                self.assertEqual(assess(quality)['ready_for_delivery'], allowed)
