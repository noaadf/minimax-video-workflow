"""Prompt integration and gate regression; these do not certify model judgement."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow.review import _editorial_review
from workflow.narrative_gate import review_manifest, require_review, CHECKS
from workflow.plan import write_json
from tests import test_narrative_gate as narrative_fixture


class StoryReviewPromptTests(unittest.TestCase):
    def test_visual_review_stages_keep_story_and_evidence_distinct(self):
        plan = {'shots': [{'id': 'S1', 'duration_seconds': 28,
                            'production_mode': 'deterministic_graphic'}], 'narration': []}
        for stage in ('final', 'preflight'):
            with self.subTest(stage=stage), patch('workflow.review.resolve', return_value={}), patch('workflow.review.ask_json') as client:
                client.return_value = ({'verdict': 'pass', 'confidence': .99, 'findings': [
                    {'severity': 'medium', 'category': 'visual_text_mismatch',
                     'issue': 'The promised experiment has no scene action', 'shot_ids': ['S1']}]}, {'model': 'test'})
                result = _editorial_review(plan, Path('unused.png'), stage=stage)
                prompt, brief = client.call_args.args
                self.assertIn('PPT-like substitution', prompt)
                self.assertIn('Long shots can still be slides', prompt)
                self.assertIn('Never demand an invented successful experiment', prompt)
                self.assertIn('mark temporal execution not assessed', prompt)
                self.assertEqual(brief['shots'], plan['shots'])
                self.assertEqual(result['status'], 'review_required')
                self.assertEqual('STAGE: GENERATION PREFLIGHT' in prompt, stage == 'preflight')
                if stage == 'preflight':
                    self.assertIn('A reference held in an animatic is expected', prompt)

    def test_narrative_ppt_finding_blocks_exact_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp)/'manifest.json'; receipt = Path(temp)/'review.json'
            write_json(manifest, narrative_fixture.NarrativeTests().plan())
            def client(prompt, brief, **kwargs):
                self.assertIn('boxes or arrows', prompt)
                self.assertIn('numeric diagram quota', prompt)
                self.assertIn('prepare a test can be a truthful ending', prompt)
                self.assertEqual(len(brief['shots']), 3)
                checks = {key: True for key in CHECKS}
                checks['causal_progression'] = False
                return {'verdict': 'revise', 'checks': checks,
                        'issues': [{'shot_ids': ['0', '1'], 'issue': 'Statements replace scene action'}]}, {'model': 'test', 'usage': {}}
            review_manifest(manifest, receipt, client)
            with self.assertRaisesRegex(ValueError, 'narrative needs revision'):
                require_review(manifest, receipt)
