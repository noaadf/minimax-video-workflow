import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from workflow.decoded_shot_evidence import build, select_frames
from workflow import media

class DecodedShotEvidenceTests(unittest.TestCase):
    def test_actual_tail_and_short_video_fail_closed(self):
        frames=[{'best_effort_timestamp_time':str(i/24)} for i in range(79)]
        rows,chosen=select_frames(frames,start=0,length=3.306)
        self.assertEqual(len(rows),79)
        self.assertEqual(chosen[0]['index'],0)
        self.assertEqual(chosen[-1]['index'],78)
        self.assertEqual(chosen[-1]['local_pts'],3.25)
        self.assertEqual(len({r['index'] for r in chosen}),24)
        with self.assertRaises(ValueError):
            select_frames(frames[:23],start=0,length=1)

    def test_nonzero_start_and_irregular_pts(self):
        pts=[i/30+(i%4)*.001 for i in range(200)]
        frames=[{'best_effort_timestamp_time':str(t)} for t in pts]
        rows,chosen=select_frames(frames,start=3.13,length=2)
        self.assertGreaterEqual(rows[0]['source_pts'],3.13)
        self.assertLess(rows[-1]['source_pts'],5.13)
        self.assertEqual(chosen[0],rows[0]);self.assertEqual(chosen[-1],rows[-1])
        for bad in ([frames[0],frames[0]], [{'best_effort_timestamp_time':'nan'}]):
            with self.assertRaises(ValueError):select_frames(bad,start=0,length=2)

    def test_rendered_tail_has_real_last_picture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);v=root/'v.mp4'
            media.ff('-f','lavfi','-i','color=blue:s=64x96:r=24:d=3.3',
                '-vf',"drawbox=x=0:y=0:w=iw:h=ih:color=red:t=fill:enable='eq(n,78)'",
                '-frames:v','79','-c:v','libx264','-pix_fmt','yuv420p',v)
            result=build(v,root/'e',length=3.306)
            self.assertEqual(result['last_frame']['index'],78)
            self.assertEqual(result['endpoints']['rendered_source_pts'],[0,3.25])
            pixels=media.run('ffmpeg','-v','error','-i',result['endpoints']['path'],
                '-vf','crop=2:2:96:48','-f','rawvideo','-pix_fmt','rgb24','-')[:3]
            self.assertGreater(pixels[0],pixels[2]+100)
            with self.assertRaises(ValueError):build(v,root/'e',length=3.306)

    def test_opt_in_review_is_bound_once_and_rejects_wrong_tail(self):
        from workflow.shot_review import review_shot, STORY_CRITERIA
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);v=root/'v.mp4'
            media.ff('-f','lavfi','-i','testsrc2=s=64x96:r=24:d=3.3',
                     '-frames:v','79','-c:v','libx264',v)
            shot={'id':'A08','duration_seconds':3.306,'storyboard':{'action':'place'}}
            cfg={'model':'test','provider':'test','base_url':'http://test'}
            answer={'checks':[{'criterion':c,'verdict':'pass','confidence':.9,
                    'evidence_seconds':[0,1.416667,3.208333],
                    'observation':'Observed placement'} for c in STORY_CRITERIA],
                    'findings':[],'not_assessed':['audio']}
            with patch('workflow.shot_review.configuration',return_value=cfg), \
                 patch('workflow.shot_review.ask_json',return_value=(answer,{})) as api:
                r=review_shot(v,shot,root/'r',decoded_endpoints=True)
                self.assertEqual(r['status'],'review_required')
                self.assertIn('Missing exact decoded',r['validation_failure'])
                self.assertEqual(r['sample_times'][-1],3.25)
                self.assertEqual(r['binding']['evidence_policy'],'decoded-first-last-pts-v1')
                again=review_shot(v,shot,root/'r',decoded_endpoints=True)
                self.assertEqual(api.call_count,1)
                self.assertEqual(again['validation_failure'],r['validation_failure'])

    def test_manifest_routes_only_explicit_evidence_correction(self):
        from workflow.shot_review import review_film
        plan={'pacing_policy':'storyboard_v1','shots':[
            {'id':'A08','duration_seconds':3.306,
             'review_evidence_policy':'decoded-first-last-pts-v1'},
            {'id':'A10','duration_seconds':3.2}]}
        with patch('workflow.shot_review.review_shot',return_value={'status':'review_required'}) as api:
            review_film(plan,'unused','unused',workers=1)
            self.assertTrue(api.call_args_list[0].kwargs['decoded_endpoints'])
            self.assertNotIn('decoded_endpoints',api.call_args_list[1].kwargs)
