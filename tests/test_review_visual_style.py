"""Local prompt/contract checks, not a claim about model judgement."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow.review import _editorial_review, editorial_review, visual_style_instruction


class VisualStyleReviewTests(unittest.TestCase):
    def plan(self, policy=None):
        p={'shots':[{'id':'S01','duration_seconds':4}], 'narration':[], 'review_mode':'api'}
        if policy is not None:p['visual_style_policy']=policy
        return p

    def invoke(self, policy, *, stage='preflight', confidence=.95, severity=None):
        answer={'verdict':'pass','confidence':confidence,'findings':[],'not_assessed':['motion','audio']}
        if severity:
            answer['findings']=[{'severity':severity,'category':'geometry','issue':'Fused bridge support','shot_ids':['S01']}]
        with patch('workflow.review.resolve',return_value={}), \
             patch('workflow.review.ask_json',return_value=(answer,{'model':'fixture'})) as api:
            result=_editorial_review(self.plan(policy),Path('unused.png'),stage=stage)
        return result, api.call_args.args

    def test_missing_empty_or_invalid_declaration_uses_conservative_default(self):
        for policy in (None,{},'', ' ',True,[] ):
            with self.subTest(policy=policy):
                text=visual_style_instruction(self.plan(policy))
                self.assertIn('VISUAL STYLE DEFAULT',text)
                self.assertIn('conservative natural',text)

    def test_explicit_cinematic_policy_replaces_default_in_both_stages(self):
        policy={'version':'cinematic_spectacle_v1','palette':'intentional teal/orange',
                'lighting':'dramatic cinematic scale, physically readable subject'}
        for stage in ('preflight','final'):
            with self.subTest(stage=stage):
                result,(prompt,brief)=self.invoke(policy,stage=stage)
                self.assertEqual(brief['visual_style_policy'],policy)
                self.assertIn('explicit aesthetic contract',prompt)
                self.assertIn('do not treat it as a defect merely',prompt)
                self.assertNotIn('VISUAL STYLE DEFAULT',prompt)
                self.assertNotIn('User visual preference: natural live-action',prompt)
                self.assertNotIn('Reject pervasive cinematic orange/teal',prompt)
                self.assertEqual(result['status'],'automatic_review_passed')

    def test_explicit_documentary_and_text_policies_remain_authored_constraints(self):
        for policy in ({'version':'documentary_realism_v1'},'Neutral documentary available light'):
            _,(prompt,brief)=self.invoke(policy)
            self.assertEqual(brief['visual_style_policy'],policy)
            self.assertIn('judge against that requirement',prompt)
            self.assertIn('violating its own limits',prompt)

    def test_all_styles_preserve_subject_material_geometry_text_and_fact_checks(self):
        for policy in (None,{'version':'cinematic_spectacle_v1'}):
            _,(prompt,_)=self.invoke(policy)
            for phrase in ('skin and material rendering','clipped detail','damaged texture',
                           'fused or malformed geometry','wrong technical structures',
                           'generated gibberish/logos','unsupported event/result','final-audio gates'):
                self.assertIn(phrase,prompt)

    def test_threshold_and_material_findings_unchanged_for_final_and_preflight(self):
        # Board-review v2 ruling 1: confidence no longer gates (any float passes with
        # clean findings); material severities still block at any confidence.
        for stage in ('preflight','final'):
            for confidence,severity,expected in ((.78,None,'automatic_review_passed'),
                (.849,None,'automatic_review_passed'),(.85,None,'automatic_review_passed'),
                (.99,'medium','review_required'),(.99,'high','review_required')):
                with self.subTest(stage=stage,confidence=confidence,severity=severity):
                    result,_=self.invoke({'version':'cinematic_spectacle_v1'},stage=stage,
                                         confidence=confidence,severity=severity)
                    self.assertEqual(result['status'],expected)
                    self.assertEqual(result['review']['confidence'],confidence)

    def test_style_is_cache_bound_and_existing_receipt_bytes_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);image=root/'contact.png';image.write_bytes(b'local fixture')
            cfg={'model':'fixture','provider':'fixture','base_url':'http://fixture'}
            answer={'verdict':'pass','confidence':.78,'findings':[],'not_assessed':[]}
            with patch.dict(os.environ,{'WORKFLOW_REVIEW_CACHE':str(root/'cache')}), \
                 patch('workflow.review.resolve',return_value=cfg), \
                 patch('workflow.review.ask_json',return_value=(answer,{'model':'fixture'})) as api:
                first=editorial_review(self.plan(),image,stage='preflight')
                existing={p:p.read_bytes() for p in (root/'cache').glob('*.json')}
                second=editorial_review(self.plan({'version':'cinematic_spectacle_v1'}),image,stage='preflight')
                again=editorial_review(self.plan(),image,stage='preflight')
                self.assertEqual(api.call_count,2)
                self.assertTrue(again['cache_hit'])
                self.assertEqual(first['status'],second['status'])
                self.assertEqual(len(list((root/'cache').glob('*.json'))),2)
                for p,data in existing.items():self.assertEqual(p.read_bytes(),data)
