import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from workflow.plan import sha256,write_json
from workflow.storyboard import reuse_preflight

class PreflightReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.voice=self.root/'voice';self.voice.write_bytes(b'voice')
        self.bed=self.root/'bed';self.bed.write_bytes(b'bed')
        self.plan={'id':'story','narration':[],'shots':[{'id':'A1','duration_seconds':4,'previsual_path':'first',
            'reference_images':['first','last'],'storyboard':{'purpose':'loading','state_after':{'boxes':2}}}]}
        self.edl={'caption_plan':copy.deepcopy(self.plan),'narration_path':str(self.voice),'music_path':str(self.bed),
          'input_sha256':{str(self.voice):sha256(self.voice),str(self.bed):sha256(self.bed)},
          'shots':[{'id':'A1','kind':'image','source_path':'first','duration_seconds':4,'purpose':'loading','source_start':0,'end_source_path':'last'}]}
        self.paths={name:self.root/(name+'.json') for name in ('manifest','edl','visual_review','preview','review')}
        write_json(self.paths['manifest'],self.plan);write_json(self.paths['edl'],self.edl)
        write_json(self.paths['visual_review'],{'status':'automatic_review_passed','review_stage':'preflight',
          'receipt':{'model':'fixture'},'review':{'verdict':'pass','confidence':.9,'findings':[]}})
        self.paths['preview'].write_bytes(b'preview');self.paths['review'].write_bytes(b'review')
        self.acceptance=self.root/'acceptance.json'
        self.rebind()
        self.runtime={**self.plan,'id':'story-runtime','approved_story_preflight':{'path':str(self.acceptance),'sha256':sha256(self.acceptance)}}
        self.voice_result={'audio_path':str(self.voice),'effective_narration':[]}
        self.bed_result={'audio_path':str(self.bed)}
    def rebind(self):
        d={'status':'ready_for_generation'}
        for k,p in self.paths.items():d[k+'_path']=str(p);d[k+'_sha256']=sha256(p)
        write_json(self.acceptance,d)
        if hasattr(self,'runtime'):self.runtime['approved_story_preflight']['sha256']=sha256(self.acceptance)
    def run_replay(self):
        with patch('workflow.narrative_gate.require_review'),patch('workflow.roughcut.validate'),\
             patch('workflow.plan.freeze_assets',side_effect=lambda p,b:p),patch('workflow.review.editorial_review') as api:
            result=reuse_preflight(self.runtime,self.voice_result,self.bed_result,self.root)
            api.assert_not_called();return result
    def test_unchanged_story_and_identical_stem_bytes_reuse_without_api(self):
        self.runtime['backend']={'type':'assets'}
        self.assertTrue(self.run_replay()['reused_approved_preflight'])
    def test_authored_state_change_invalidates_reuse(self):
        self.runtime=copy.deepcopy(self.runtime);self.runtime['shots'][0]['storyboard']['state_after']['boxes']=1
        with self.assertRaisesRegex(ValueError,'Authored story changed'):self.run_replay()
    def test_runtime_audio_change_invalidates_reuse(self):
        changed=self.root/'changed';changed.write_bytes(b'different voice');self.voice_result['audio_path']=str(changed)
        with self.assertRaisesRegex(ValueError,'production audio differs'):self.run_replay()
    def test_changed_evidence_invalidates_reuse(self):
        self.paths['preview'].write_bytes(b'replaced')
        with self.assertRaisesRegex(ValueError,'evidence changed'):self.run_replay()
    def test_false_final_stage_approval_cannot_be_replayed_and_confidence_no_longer_gates(self):
        # Board-review v2 ruling 1: confidence exits the gating rule, so reuse must
        # not resurrect the retired 0.85 check ("new review passes, reuse fails").
        x=json.loads(self.paths['visual_review'].read_text());x['review_stage']='final'
        write_json(self.paths['visual_review'],x);self.rebind()
        with self.assertRaisesRegex(ValueError,'planning visual review required'):self.run_replay()
        x['review_stage']='preflight';x['review']['confidence']=.8
        write_json(self.paths['visual_review'],x);self.rebind()
        self.assertTrue(self.run_replay()['reused_approved_preflight'])
    def test_hash_bound_but_wrong_edl_picture_still_rejected(self):
        self.edl['shots'][0]['source_path']='wrong shot';write_json(self.paths['edl'],self.edl);self.rebind()
        with self.assertRaisesRegex(ValueError,'EDL pictures differ'):self.run_replay()
