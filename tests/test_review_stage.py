import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from workflow.review import editorial_review

class ReviewStageTests(unittest.TestCase):
    def test_scope_is_cache_bound_and_confidence_does_not_gate(self):
        # Board-review v2 ruling 1: the uncalibrated self-reported confidence float
        # exits the gating rule (docs/board_review_v2_design.md). Verdict pass with
        # no medium/high findings passes at any confidence; a medium finding still
        # blocks even at confidence .95.
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);image=root/'sheet.jpg';image.write_bytes(b'grid')
            cfg={'model':'test','provider':'test','base_url':'http://test'}
            answer={'verdict':'pass','confidence':.75,'findings':[],'not_assessed':['motion']}
            blocking={'verdict':'pass','confidence':.95,'findings':[{'severity':'medium'}],'not_assessed':[]}
            plan={'shots':[{'id':'A1','duration_seconds':4}],'narration':[],'pacing_policy':'storyboard_v1'}
            with patch.dict(os.environ,{'WORKFLOW_REVIEW_CACHE':str(root/'cache')}),\
                 patch('workflow.review.resolve',return_value=cfg),\
                 patch('workflow.review.ask_json',return_value=(answer,{'model':'test'})) as api:
                a=editorial_review(plan,image,stage='preflight')
                b=editorial_review(plan,image,stage='final')
                editorial_review(plan,image,stage='preflight')
            self.assertEqual(api.call_count,2)
            self.assertEqual(a['status'],'automatic_review_passed');self.assertEqual(b['status'],'automatic_review_passed')
            self.assertEqual(a['review_stage'],'preflight');self.assertEqual(b['review_stage'],'final')
            self.assertIn('GENERATION PREFLIGHT',api.call_args_list[0].args[0])
            self.assertNotIn('GENERATION PREFLIGHT',api.call_args_list[1].args[0])
            cache2=root/'cache2'
            with patch.dict(os.environ,{'WORKFLOW_REVIEW_CACHE':str(cache2)}),\
                 patch('workflow.review.resolve',return_value=cfg),\
                 patch('workflow.review.ask_json',return_value=(blocking,{'model':'test'})):
                self.assertEqual(editorial_review(plan,image,stage='preflight')['status'],'review_required')
    def test_unknown_scope_rejected(self):
        with self.assertRaises(ValueError):editorial_review({},'unused',stage='bypass')
