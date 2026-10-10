"""Stage separation and strict-gate regressions using the observed A10 failure."""
import copy
import unittest

from workflow.planning_review_scope import assess, request_binding


def answer(confidence=.95):
    return {'review_stage': 'preflight', 'checks': [{
        'criterion': 'reach', 'verdict': 'pass', 'confidence': confidence,
        'evidence_ids': ['mid', 'end'],
        'observable_basis': 'Hands low below mirrors; end grips fixed.',
        'confidence_reasons': [{'scope': 'planning', 'issue': 'Visible route avoids the mirror.'}]}],
        'findings': [], 'deferred_checks': [{'scope': 'motion',
            'issue': 'Actual hand rise and 3D clearance not generated or observed.'}]}


class PlanningReviewScopeTests(unittest.TestCase):
    def test_low_confidence_remains_held_despite_planning_pass_and_deferred_motion(self):
        observed = answer(.72)
        original = copy.deepcopy(observed)
        result = assess(observed, ['reach'], ['mid', 'end'])
        self.assertEqual(result['status'], 'planning_review_required')
        self.assertEqual(result['blocking_criteria'], ['reach'])
        self.assertEqual(observed, original)
        self.assertEqual(result['confidence_threshold'], .85)

    def test_motion_in_confidence_basis_blocks_without_reroll_or_score_override(self):
        observed = answer()
        observed['checks'][0]['confidence_reasons'] = [{
            'scope': 'motion', 'issue': 'No generated hand-rise interpolation exists.'}]
        result = assess(observed, ['reach'], ['mid', 'end'])
        self.assertEqual(result['status'], 'planning_review_required')
        self.assertEqual(len(result['scope_violations']), 1)
        self.assertEqual(result['review']['checks'][0]['confidence'], .95)
        self.assertFalse(result['automatic_replay'])

    def test_visible_mirror_collision_blocks_even_with_high_score_and_motion_limit(self):
        observed = answer(.99)
        finding = {'severity': 'medium', 'issue': 'Fingers overlap mirror top.', 'evidence_ids': ['mid']}
        observed['findings'] = [finding]
        result = assess(observed, ['reach'], ['mid', 'end'])
        self.assertEqual(result['status'], 'planning_review_required')
        self.assertEqual(result['material_findings'], [finding])

    def test_planning_readiness_never_certifies_motion_or_film(self):
        result = assess(answer(), ['reach'], ['mid', 'end'])
        self.assertEqual(result['status'], 'planning_ready')
        self.assertFalse(result['actual_motion_approved'])
        self.assertFalse(result['whole_film_approved'])

    def test_final_or_legacy_result_cannot_be_relabelled_as_planning(self):
        for stage in ('final', None):
            observed = answer()
            observed['review_stage'] = stage
            with self.assertRaises(ValueError):
                assess(observed, ['reach'], ['mid', 'end'])

    def test_invented_evidence_or_nonfinite_confidence_rejected(self):
        for change in (lambda a: a['checks'][0].update(evidence_ids=['imaginary']),
                       lambda a: a['checks'][0].update(confidence=float('nan')),
                       lambda a: a['checks'][0].update(confidence=True)):
            observed = answer(); change(observed)
            with self.assertRaises(ValueError):
                assess(observed, ['reach'], ['mid', 'end'])

    def test_changed_evidence_is_bound_without_touching_existing_cache(self):
        a = request_binding('manifest', {'mid': 'first'}, {'stage': 'preflight'})
        b = request_binding('manifest', {'mid': 'second'}, {'stage': 'preflight'})
        self.assertNotEqual(a, b)

    def test_policy_or_label_change_does_not_authorize_same_evidence_replay(self):
        with self.assertRaisesRegex(ValueError, 'Unchanged evidence'):
            request_binding('new-manifest-label', {'renamed-mid': 'same-frame'},
                            {'new-policy': True}, previous_evidence_hashes={'old-mid': 'same-frame'})


if __name__ == '__main__':
    unittest.main()
