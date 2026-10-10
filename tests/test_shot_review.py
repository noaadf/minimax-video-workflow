import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow.shot_review import assess, review_shot, CRITERIA


def valid():
    return {'checks':[{'criterion':c,'verdict':'pass','confidence':.95,'evidence_seconds':[0,.5],
                       'observation':'Observed stable connected shape.'} for c in CRITERIA],
            'findings':[],'not_assessed':['audio']}


class ShotReviewTests(unittest.TestCase):
    def test_uncertainty_and_material_defects_never_pass(self):
        a=valid();self.assertEqual(assess(a,[0,.5]),'sampled_checks_passed')
        a['checks'][0]['verdict']='review';self.assertEqual(assess(a,[0,.5]),'review_required')
        a=valid();a['findings']=[{'severity':'high','time_seconds':.5,'issue':'Panel separates.'}]
        self.assertEqual(assess(a,[0,.5]),'review_required')
    def test_missing_or_invented_evidence_rejected(self):
        for alter in (lambda a:a['checks'].pop(),lambda a:a['checks'][0].update(evidence_seconds=[9]),
                      lambda a:a['checks'][0].update(confidence=float('nan'))):
            a=valid();alter(a)
            with self.assertRaises(ValueError):assess(a,[0,.5])
    def test_cache_binds_clip_narration_and_api_failure_closed(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);v=root/'clip.mp4';v.write_bytes(b'first')
            def sample(video,sheet,**kwargs):
                Path(sheet).write_bytes(b'sheet')
                return [round(i/6,4) for i in range(24)]
            cfg={'model':'test','provider':'test','base_url':'http://test'}
            with patch('workflow.shot_review.configuration',return_value=cfg),patch('workflow.shot_review.sample_sheet',side_effect=sample),patch('workflow.shot_review.ask_json',return_value=(valid(),{})) as api:
                shot={'id':'S1','duration_seconds':4}
                a=review_shot(v,shot,root/'r');b=review_shot(v,shot,root/'r')
                self.assertEqual(api.call_count,1);self.assertEqual(a['binding_sha256'],b['binding_sha256'])
                review_shot(v,shot,root/'r',narration=[{'text':'different'}]);self.assertEqual(api.call_count,2)
                v.write_bytes(b'changed');api.side_effect=TimeoutError()
                c=review_shot(v,shot,root/'r');self.assertEqual(c['status'],'review_required')
                self.assertFalse(c['content_approved'])

    def test_exact_sample_does_not_move_scene_cut_backward(self):
        from workflow import media
        from workflow.shot_review import sample_sheet
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);video=root/'cut.mp4'
            media.ff('-f','lavfi','-i','color=blue:s=64x96:r=24:d=20',
                     '-vf',"drawbox=x=0:y=0:w=iw:h=ih:color=red:t=fill:enable='gte(n,345)'",
                     '-c:v','libx264','-pix_fmt','yuv420p',video)
            exact=root/'exact.jpg';old=root/'old.jpg'
            times=sample_sheet(video,exact,start=0,length=20)
            self.assertAlmostEqual(times[17],14.1667,places=3)
            media.ff('-i',video,'-vf','fps=1.2,scale=270:480,tile=6x4','-frames:v','1',old)
            def center(path):
                # Cell17 (third row, sixth column) represents target14.1667s.
                return media.run('ffmpeg','-v','error','-i',path,'-vf',
                    'crop=2:2:1484:1198','-f','rawvideo','-pix_fmt','rgb24','-')[:3]
            r,g,b=center(exact);self.assertGreater(b,r+100)
            r,g,b=center(old);self.assertGreater(r,b+100)

    def test_samples_use_local_time_for_nonzero_start(self):
        from workflow import media
        from workflow.shot_review import sample_sheet
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);video=root/'v.mp4'
            media.ff('-f','lavfi','-i','testsrc2=s=64x96:r=24:d=8','-c:v','libx264',video)
            times=sample_sheet(video,root/'s.jpg',start=3,length=4)
            self.assertEqual(times[0],0)
            self.assertAlmostEqual(times[-1],23/6,places=3)

    def test_larger_endpoints_are_exact_sampled_frames(self):
        from workflow import media
        from workflow.shot_review import sample_sheet,endpoint_sheet
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);video=root/'v.mp4'
            media.ff('-f','lavfi','-i','testsrc2=s=64x96:r=24:d=8','-c:v','libx264',video)
            times=sample_sheet(video,root/'grid.jpg',start=3.13,length=3.404)
            self.assertEqual(endpoint_sheet(video,root/'detail.jpg',start=3.13,length=3.404,times=times),[times[0],times[-1]])
            bad=list(times);bad[-1]+=.015
            with self.assertRaisesRegex(ValueError,'differs'):
                endpoint_sheet(video,root/'bad.jpg',start=3.13,length=3.404,times=bad)

class RoutingTests(unittest.TestCase):
    def test_normal_film_needs_no_dense_api(self):
        from workflow.shot_review import review_film
        plan={'shots':[{'id':'S1','duration_seconds':4}]}
        with patch('workflow.shot_review.review_shot') as api:
            r=review_film(plan,'unused','unused',editorial={'review':{'findings':[]}},screening={})
            self.assertEqual(r['status'],'no_exception_review_needed');api.assert_not_called()
    def test_hard_failure_never_spends_api(self):
        from workflow.shot_review import review_film
        with patch('workflow.shot_review.review_shot') as api:
            r=review_film({'shots':[]},'unused','unused',screening={'hard_failed':True})
            self.assertEqual(r['status'],'review_required');api.assert_not_called()
    def test_limit_and_subtitle_routing(self):
        from workflow.shot_review import select_exceptions
        plan={'shots':[{'id':'S1','duration_seconds':4},{'id':'S2','duration_seconds':4}]}
        editorial={'review':{'findings':[{'severity':'high','category':'subtitle','shot_ids':['S1']}]}}
        self.assertEqual(select_exceptions(plan,editorial,{})['selected'],[])
        editorial['review']['findings'][0].update(category='geometry',shot_ids=['S1','S2'])
        r=select_exceptions(plan,editorial,{})
        self.assertEqual(r['selected'],['S1']);self.assertEqual(r['deferred'],['S2'])

class ProgrammaticTests(unittest.TestCase):
    def test_timeline_rejects_gap_and_overlap(self):
        from workflow.programmatic_qc import timeline_checks
        p={'duration_seconds':4,'shots':[{'duration_seconds':4}], 'narration':[{'start':0,'end':3},{'start':2,'end':4}]}
        self.assertFalse(timeline_checks(p)['narration_timeline_valid'])
        p['narration']=[];p['shots'][0]['duration_seconds']=3
        self.assertFalse(timeline_checks(p)['shot_timeline_complete'])
    def test_cheap_black_screening_without_model(self):
        from workflow.programmatic_qc import screen_video
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);v=p/'v';v.write_bytes(b'v')
            info={'streams':[{'codec_type':'video','width':1080,'height':1920,'duration':'4'}],'format':{'duration':'4'}}
            class Done:
                returncode=0;stdout='0, x, a\n0, y, a\n';stderr='black_start:1 black_end:2 black_duration:1'
            with patch('workflow.programmatic_qc.media.probe',return_value=info),patch('workflow.programmatic_qc.subprocess.run',return_value=Done()):
                r=screen_video(v,p/'r',{'duration_seconds':4,'shots':[{'duration_seconds':4}]})
                self.assertEqual(r['status'],'anomaly');self.assertFalse(r['content_approved'])
