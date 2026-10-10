"""Sanitized, offline regressions for review follow-up and request scopes."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from workflow.audit_disposition import disposition
from workflow.plan import sha256
from workflow.programmatic_qc import llm_review_scope, screen_video
from workflow.review_state import classify_review, mark_review_required, review_dashboard


def review(confidence=.95, **changes):
    return {'verdict': 'pass', 'confidence': confidence, 'findings': [], **changes}


def measured(**changes):
    return {'binding': {'video_sha256': 'a' * 64}, 'hard_failed': False,
            'checks': {'duration': True, 'video_duration': True, 'screening_decode': True,
                       'shot_durations_positive': True, 'shot_timeline_complete': True,
                       'narration_timeline_valid': True, 'vertical': True},
            'interior_black_intervals': [], **changes}


class ReviewTaxonomyTests(unittest.TestCase):
    def test_point78_whole_board_pass_goes_to_human_without_generation(self):
        original = {'status': 'review_required', 'review_stage': 'preflight',
                    'receipt': {'model': 'fixture'}, 'review': review(.78)}
        before = copy.deepcopy(original)
        result = classify_review(original)
        self.assertEqual(result['status'], 'needs_human_attention')
        self.assertEqual(result['next_action'], 'add_to_human_review')
        self.assertFalse(result['regeneration_eligible'])
        self.assertFalse(result['automatic_regeneration'])
        self.assertFalse(result['delivery_approved'])
        self.assertEqual(result['confidence_threshold'], .85)
        self.assertEqual(original, before)

    def test_evidence_bound_visual_defect_is_rework(self):
        finding = {'category': 'geometry', 'severity': 'high', 'issue': 'Two limbs visibly fused',
                   'shot_ids': ['S01'], 'time_seconds': 0}
        result = classify_review(review(verdict='reject', findings=[finding]))
        self.assertEqual(result['status'], 'visual_rework_required')
        self.assertEqual(result['next_action'], 'prepare_visual_rework')
        self.assertTrue(result['regeneration_eligible'])
        self.assertEqual(result['visual_defects'], [finding])

    def test_raw_three_sample_audit_schema_is_recognized(self):
        result = classify_review(review(verdict='reject', findings=[{
            'category': 'object_fusion', 'severity': 'medium', 'sample_seconds': [0],
            'observation': 'Front wheel fused to the frame'}]))
        self.assertEqual(result['status'], 'visual_rework_required')

    def test_unattributed_or_uncertain_visual_findings_do_not_regenerate(self):
        base = {'category': 'geometry', 'severity': 'medium', 'issue': 'Occluded contact point'}
        for extra in ({}, {'shot_ids': ['S01'], 'requires_interpretation': True},
                      {'shot_ids': ['S01'], 'confirmed': False}):
            with self.subTest(extra=extra):
                result = classify_review(review(findings=[{**base, **extra}]))
                self.assertEqual(result['status'], 'needs_human_attention')
                self.assertFalse(result['regeneration_eligible'])

    def test_nonvisual_defects_never_route_to_image_rework(self):
        for category in ('fact', 'subtitle', 'audio', 'speech', 'unknown'):
            result = classify_review(review(findings=[{'category': category, 'severity': 'high',
                                                       'issue': 'Requires a separate check', 'shot_ids': ['S01']}]))
            self.assertEqual(result['status'], 'needs_human_attention')
            self.assertFalse(result['regeneration_eligible'])

    def test_static_motion_criterion_is_a_capability_mismatch(self):
        record = {'review_stage': 'preflight', 'review': {'checks': [{
            'criterion': 'action_result', 'verdict': 'reject', 'confidence': .95,
            'observation': 'Static keyframe cannot prove execution', 'evidence_seconds': [0]}],
            'findings': []}}
        result = classify_review(record)
        self.assertEqual(result['status'], 'needs_human_attention')
        self.assertIn('capability_mismatch', result['reasons'])
        self.assertFalse(result['regeneration_eligible'])

    def test_deferred_motion_does_not_erase_a_real_static_defect(self):
        result = classify_review({'review_stage': 'preflight', 'review': review(findings=[{
            'category': 'geometry', 'severity': 'high', 'issue': 'Visible mirror collision',
            'evidence_ids': ['middle']}], not_assessed=['motion'])})
        self.assertEqual(result['status'], 'visual_rework_required')

    def test_motion_confidence_basis_is_not_a_planning_defect(self):
        record = {'review_stage': 'preflight', 'review': {'checks': [{
            'criterion': 'structure', 'verdict': 'pass', 'confidence': .95,
            'confidence_reasons': [{'scope': 'motion', 'issue': 'Movement not visible in keyframes'}]}],
            'findings': []}}
        result = classify_review(record)
        self.assertEqual(result['status'], 'needs_human_attention')
        self.assertIn('capability_mismatch', result['reasons'])

    def test_malformed_scope_reasons_require_attention_without_crashing(self):
        result = classify_review({'review': {'checks': [{
            'criterion': 'structure', 'verdict': 'pass', 'confidence': .95,
            'confidence_reasons': 3}], 'findings': []}})
        self.assertEqual(result['status'], 'needs_human_attention')

    def test_low_confidence_invalid_or_inconclusive_results_stay_human(self):
        for confidence in (.78, None, True, float('nan'), float('inf'), 1.1):
            self.assertEqual(classify_review(review(confidence))['status'], 'needs_human_attention')
        self.assertEqual(classify_review(review(verdict='reject'))['status'], 'needs_human_attention')
        self.assertEqual(classify_review({'verdict': 'pass', 'confidence': .95})['status'], 'needs_human_attention')

    def test_threshold_is_inclusive_and_low_advisories_do_not_trigger_polish(self):
        result = classify_review(review(.85, findings=[{
            'category': 'visual_quality', 'severity': 'low', 'issue': 'Small aesthetic preference'}]))
        self.assertEqual(result['status'], 'review_passed')
        self.assertEqual(len(result['advisories']), 1)
        self.assertFalse(result['regeneration_eligible'])

    def test_dashboard_separates_human_and_visual_lanes(self):
        lists = review_dashboard([{'id': 'human', 'review': review(.78)},
                                  {'id': 'visual', 'review': review(findings=[{
                                      'category': 'geometry', 'severity': 'high',
                                      'issue': 'Visible fusion', 'shot_ids': ['S01']}])}])
        self.assertEqual([x['id'] for x in lists['needs_human_attention']], ['human'])
        self.assertEqual([x['id'] for x in lists['visual_rework_required']], ['visual'])

    def test_merge_preserves_rejection_and_classifies_after_late_findings(self):
        editorial = {'review': review(verdict='reject'), 'receipt': {'model': 'fixture'}}
        merged = mark_review_required(editorial)
        merged['findings'].append({'category': 'geometry', 'severity': 'high',
                                  'issue': 'Visible collision', 'shot_ids': ['S01']})
        self.assertEqual(merged['verdict'], 'reject')
        self.assertEqual(classify_review(editorial)['status'], 'visual_rework_required')

    def test_audit_legacy_disposition_and_new_human_lane_coexist(self):
        result = disposition({'finished_at': 1, 'review': {'shots': [
            {'shot_id': 'S01', **review(.78)}]}})
        self.assertEqual(result['shots'][0]['disposition'], 'uncertainty_only_no_regeneration')
        self.assertEqual(result['shots'][0]['review_state'], 'needs_human_attention')
        self.assertEqual(len(result['dashboard']['needs_human_attention']), 1)

    def test_bound_minor_detail_requires_the_original_confidence_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / 'synthetic.mp4'; video.write_bytes(b'sanitized-video')
            sheet = Path(tmp) / 'synthetic.jpg'; sheet.write_bytes(b'sanitized-grid')
            for confidence, expected in ((.78, 'review_required'), (.95, 'minor_only_no_regeneration')):
                detail = {'shot_id': 'S01', 'review_execution_status': 'completed',
                          'binding': {'video_sha256': sha256(video)}, 'evidence_sheet': str(sheet),
                          'evidence_sha256': sha256(sheet), 'review': {'checks': [{
                              'criterion': criterion, 'verdict': 'pass', 'confidence': confidence,
                              'observation': 'Visible stable geometry', 'evidence_seconds': [0]}
                              for criterion in ('structure', 'scene_contents', 'narration_alignment')],
                              'findings': [{'severity': 'low', 'issue': 'Minor color preference'}]}}
                record = {'finished_at': 1, 'review': {'shots': [{'shot_id': 'S01', **review(verdict='review')}]},
                          'evidence': [{'shot_id': 'S01', 'video_path': str(video), 'source_sha256': sha256(video)}],
                          'exceptions': [detail]}
                result = disposition(record)
                self.assertEqual(result['shots'][0]['disposition'], expected)
                self.assertFalse(result['delivery_approved'])

    def test_changed_detailed_evidence_cannot_authorize_rework(self):
        result = disposition({'finished_at': 1, 'review': {'shots': [{'shot_id': 'S01', **review()}]},
                              'exceptions': [{'shot_id': 'S01', 'review_execution_status': 'completed',
                                              'review': review(verdict='reject')}]})
        self.assertEqual(result['shots'][0]['review_state'], 'needs_human_attention')
        self.assertFalse(result['shots'][0]['regeneration_eligible'])

    def test_known_bound_failure_cannot_be_overridden_by_minor_detail(self):
        result = disposition({'finished_at': 1, 'review': {'shots': [{
            'shot_id': 'S01', **review(findings=[{'origin': 'sha_bound_known_material_failure'}])}]},
            'exceptions': [{'shot_id': 'S01', 'review': review()}]})
        self.assertEqual(result['shots'][0]['review_state'], 'visual_rework_required')
        self.assertEqual(result['shots'][0]['disposition'], 'material_repair_required')

    def test_failed_audit_requires_human_attention_and_never_generation(self):
        result = disposition({'status': 'audit_failed', 'finished_at': 1})
        self.assertEqual(result['review_state'], 'needs_human_attention')
        self.assertFalse(result['automatic_regeneration'])
        self.assertFalse(result['delivery_approved'])


class ProgrammaticReviewScopeTests(unittest.TestCase):
    def test_programmatic_passed_dimensions_do_not_enter_llm_request(self):
        report = measured()
        report['checks'].update(volume=True, subtitle_alignment=True)
        before = copy.deepcopy(report)
        result = llm_review_scope(report, ['duration', 'black_frames', 'volume', 'subtitle_alignment',
                                          'narrative_continuity', 'composition', 'action_continuity'])
        self.assertEqual(result['criteria'], ['narrative_continuity', 'composition', 'action_continuity'])
        self.assertEqual(result['excluded_programmatic_dimensions'],
                         ['duration', 'black_frames', 'volume', 'subtitle_alignment'])
        self.assertEqual(report, before)

    def test_timeline_pass_does_not_certify_volume_or_subtitle_alignment(self):
        result = llm_review_scope(measured(), ['volume', 'subtitle_alignment', 'composition'])
        self.assertEqual(result['excluded_programmatic_dimensions'], [])
        self.assertEqual(result['pending_programmatic_dimensions'], ['volume', 'subtitle_alignment'])
        self.assertEqual(result['criteria'], ['composition'])

    def test_wrong_video_binding_or_missing_measurement_does_not_exclude(self):
        for report in (measured(binding={}), measured()):
            result = llm_review_scope(report, ['duration', 'composition'], video_sha256='b' * 64)
            self.assertEqual(result['excluded_programmatic_dimensions'], [])
            self.assertIn('duration', result['pending_programmatic_dimensions'])
            self.assertTrue(result['model_calls_blocked'])
            self.assertFalse(result['model_review_needed'])

    def test_decode_failure_and_black_anomalies_are_not_auto_passed(self):
        for report in (measured(checks={'screening_decode': False}),
                       measured(interior_black_intervals=[{'start': 1, 'end': 2}]),
                       measured(interior_black_intervals=None)):
            self.assertNotIn('black_frames', llm_review_scope(report)['programmatic_passed_dimensions'])

    def test_nonboolean_checks_cannot_certify_measurements(self):
        result = llm_review_scope(measured(checks={'volume': 1}), ['volume'])
        self.assertEqual(result['excluded_programmatic_dimensions'], [])

    def test_hard_qc_failure_suppresses_all_model_requests(self):
        result = llm_review_scope(measured(hard_failed=True))
        self.assertEqual(result['criteria'], [])
        self.assertFalse(result['model_review_needed'])
        self.assertTrue(result['model_calls_blocked'])

    def test_static_preflight_defers_motion_without_replacing_cache_separation(self):
        result = llm_review_scope(measured(), stage='preflight')
        self.assertEqual(result['criteria'], ['narrative_continuity', 'composition'])
        self.assertEqual(result['deferred_checks'], ['action_continuity'])
        self.assertIn('action_continuity', llm_review_scope(measured())['criteria'])
        with self.assertRaises(ValueError):
            llm_review_scope(measured(), stage='bypass')

    def test_screen_video_persists_scoped_measurements_without_api_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / 'video.mp4'; video.write_bytes(b'synthetic')
            info = {'streams': [{'codec_type': 'video', 'width': 1080, 'height': 1920,
                                 'duration': '2'}], 'format': {'duration': '2'}}
            plan = {'duration_seconds': 2, 'shots': [{'duration_seconds': 2}], 'narration': []}
            with patch('workflow.programmatic_qc.media.probe', return_value=info), \
                 patch('workflow.programmatic_qc.subprocess.run', return_value=SimpleNamespace(
                     returncode=0, stderr='', stdout='0,hash1\n0,hash2\n')), \
                 patch('workflow.llm.ask_json', side_effect=AssertionError('No API calls')) as api:
                first = screen_video(video, Path(tmp) / 'qc', plan)
                second = screen_video(video, Path(tmp) / 'qc', plan)
            api.assert_not_called()
            self.assertEqual(first, second)
            self.assertFalse(first['content_approved'])
            self.assertIn('black_frames', first['llm_review_scope']['programmatic_passed_dimensions'])
            self.assertEqual(first['llm_review_scope']['criteria'],
                             ['narrative_continuity', 'composition', 'action_continuity'])
            saved = json.loads(next((Path(tmp) / 'qc').glob('*.json')).read_text())
            self.assertEqual(saved['llm_review_scope'], first['llm_review_scope'])


if __name__ == '__main__':
    unittest.main()
