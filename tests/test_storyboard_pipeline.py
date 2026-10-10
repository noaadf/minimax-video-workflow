import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import media, storyboard
from workflow.plan import validate, write_json, sha256, generate, digest
from workflow.queue import Queue
from workflow.runner import submit, execute, StageError


def film():
    p = json.loads(Path('workflow/examples/smoke_bridge.json').read_text())
    p.update(id='story_test', duration_seconds=16, pacing_policy='storyboard_v1',
             caption_policy='speech_aligned_v1')
    p['story_contract'] = {'central_question':'能否把箱子安全固定？', 'story_one_sentence':'买家放好箱子后固定绑带。',
        'protagonist':'买家', 'goal':'安全装货', 'obstacle':'箱子还没固定', 'resolution':'箱子放稳且绑带拉紧',
        'beats':[{'id':'load','caused_by':None,'action':'放下箱子','result':'箱子在车斗'},
                 {'id':'secure','caused_by':'load','action':'拉紧绑带','result':'绑带拉紧'}]}
    p['narration'] = [{'start':.2,'end':4,'text':'先装货，再固定。','fact_ids':[],
                      'caption_chunks':['先装货，','再固定。'], 'caption_lines':[['先装货，'],['再固定。']]}]
    p['shots'] = []
    states = [{'box':'held','strap':'loose'}, {'box':'bed','strap':'loose'}, {'box':'bed','strap':'taut'}]
    for i, beat in enumerate(p['story_contract']['beats']):
        p['shots'].append({'id':f'S{i+1}', 'camera':'medium', 'duration_seconds':8,
            'duration_reason':'完整展示动作及结果', 'prompt':['Buyer lowers a carton into the cargo bed.',
                'Buyer pulls the threaded strap until taut.'][i], 'fact_ids':[], 'seed':1,
            'storyboard':{'beat_id':beat['id'],'scene_id':'yard','purpose':beat['result'],
                'attention_target':'手和货物','action':beat['action'],'result':beat['result'],
                'cut_reason':'动作结束，进入下一步骤','state_before':copy.deepcopy(states[i]), 'state_after':copy.deepcopy(states[i+1]),
                'transition':{'kind':'establish' if i==0 else 'continuous'}}})
    return p


class StoryContractTests(unittest.TestCase):
    def test_review_contact_covers_caption_midpoints_and_shot_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'contact.jpg'
            with patch('workflow.media.probe', return_value={'format': {'duration': '16'}}), \
                 patch('workflow.media.ff'), \
                 patch('workflow.caption_timing.events', return_value=[{'start': 1, 'end': 2}]):
                media.review_contact('unused.mp4', dest, plan=film())
            times = json.loads(Path(str(dest) + '.samples.json').read_text())['times']
            for expected in [.1, 1.5, 4, 7.9, 8.1, 12, 15.9]:
                self.assertIn(expected, times)

    def test_preflight_rejects_missing_duration_reason_before_render_or_review(self):
        p = film()
        p['shots'][0].pop('duration_reason')
        with patch('workflow.roughcut.render') as render, \
             patch('workflow.narrative_gate.review_manifest') as review:
            with self.assertRaisesRegex(ValueError, 'duration_reason'):
                storyboard.preflight(p, {}, {}, Path('unused'))
        render.assert_not_called()
        review.assert_not_called()

    def test_too_short_action_slot_is_rejected_before_remote_generation(self):
        from workflow.runner import visual_generation_request
        p=film();s=p['shots'][0];s['duration_seconds']=1
        with self.assertRaisesRegex(ValueError,'before dispatch'):
            visual_generation_request(p,s,{'id':'story_test:S1'},{'remote_root':'/worker'})
    def test_requests_skip_completed_assets_and_preserve_source_frame_work(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=film();first=p['shots'][0]
            for name in ('start.png','end.png','source.mp4'):(root/name).write_bytes(b'fixture')
            first.update(previsual_path=str(root/'start.png'),reference_images=[str(root/'start.png'),str(root/'end.png')])
            p['shots'][1]['source_path']=str(root/'source.mp4')
            path=root/'plan.json';write_json(path,p)
            storyboard.write_keyframe_requests(p,path,root/'requests.json')
            data=json.loads((root/'requests.json').read_text())
            self.assertEqual([r['shot_id'] for r in data['requests']],['S2'])
            self.assertEqual(data['requests'][0]['kind'],'source_previsual')
            (root/'source.mp4').unlink()
            self.assertIn('S2:source_path',storyboard.missing_previsuals(p,root))

    def test_missing_assets_produce_hash_bound_requests_without_dispatch(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'plan.json';write_json(path,film())
            target=Path(d)/'keyframes.json'
            storyboard.write_keyframe_requests(film(),path,target)
            data=json.loads(target.read_text())
            self.assertEqual(data['manifest_sha256'],sha256(path))
            self.assertEqual([r['reference_count'] for r in data['requests']],[2,2])
            self.assertEqual(data['requests'][1]['state_before']['strap'],'loose')
            self.assertFalse(data['generation_task_submitted'])

    def test_two_purposeful_shots_need_no_angle_quota(self):
        validate(film())

    def test_angle_only_padding_rejected(self):
        p=film();p['shots'][1]['storyboard']['state_after']=p['shots'][1]['storyboard']['state_before']
        with self.assertRaisesRegex(ValueError,'unchanged shot'):validate(p)

    def test_return_after_cutaway_cannot_reset_cargo(self):
        p=film();cut=copy.deepcopy(p['shots'][0]);cut['id']='service';cut['duration_seconds']=2
        cut['prompt']='A worker packs tools at a separate service bench.'
        cut['storyboard'].update(scene_id='service',state_before={'tools':'loose'},state_after={'tools':'packed'},
                                transition={'kind':'cutaway'})
        p['shots'].insert(1,cut);p['duration_seconds']=18
        p['shots'][-1]['storyboard'].update(state_before={'box':'ground','strap':'loose'},transition={'kind':'return'})
        with self.assertRaisesRegex(ValueError,'state jump'):validate(p)

    def test_explicit_ellipsis_allows_only_named_changes(self):
        p=film();row=p['shots'][1]['storyboard'];row['state_before']['box']='other_bed'
        row['transition']={'kind':'ellipsis','allowed_changes':['box'],'explanation':'明确展示准备过程省略'}
        validate(p)
        row['state_before']['strap']='missing'
        with self.assertRaisesRegex(ValueError,'state jump'):validate(p)

    def test_reusing_same_footage_window_rejected(self):
        p=film()
        for s in p['shots']:s.update(source_path='/same.mp4',source_start=0)
        with self.assertRaisesRegex(ValueError,'repeated source'):validate(p)

    def test_planner_defaults_to_story_and_semantic_subtitles(self):
        p=film();brief={k:p[k] for k in ('id','template','facts','music')}
        with tempfile.TemporaryDirectory() as d, patch('workflow.llm.ask_json',return_value=(p,{'model':'mock'})) as api,\
                patch('workflow.engagement.enrich'):
            result=generate(brief,Path(d)/'plan.json')
        self.assertEqual(result['pacing_policy'],'storyboard_v1')
        instruction=api.call_args.args[0]
        self.assertIn('state_before',instruction)
        self.assertIn('allowed_changes',instruction)
        self.assertNotIn('At least 6 shots',instruction)


class StoryQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.q=Queue(self.root/'q.sqlite');self.p=film()
        for i,s in enumerate(self.p['shots']):
            keyframe=self.root/f'frame{i}.png';keyframe.write_bytes(b'fixture')
            source=self.root/f'clip{i}.mp4';source.write_bytes(b'fixture')
            s.update(previsual_path=str(keyframe),source_path=str(source))
        self.path=self.root/'plan.json'

    def submit(self):
        write_json(self.path,self.p)
        with patch('workflow.runner.freeze_assets',side_effect=lambda p,base:{**p,'input_sha256':{}}):
            return submit(self.q,self.path,self.root)

    def test_missing_keyframe_never_enqueues_generation(self):
        self.p['shots'][0].pop('previsual_path')
        with self.assertRaisesRegex(ValueError,'awaiting_storyboard'):self.submit()
        self.assertEqual(self.q.status(),[])

    def test_generation_waits_for_actual_voice_music_and_preflight(self):
        self.submit()
        self.assertIsNone(self.q.claim(['cpu'],stages={'shot'}))
        for stage in ('voice','music'):
            self.q.finish(self.q.claim(['cpu'],stages={stage}),result={'ok':True})
        self.assertIsNone(self.q.claim(['cpu'],stages={'shot'}))
        pre=self.q.claim(['cpu'],stages={'story_preflight'});self.assertIsNotNone(pre)
        self.q.finish(pre,error='keyframe review rejected')
        self.assertIsNone(self.q.claim(['cpu'],stages={'shot'}))
        self.assertTrue(all(t['status']=='blocked' for t in self.q.status() if t['stage']=='shot'))

    def test_direct_shot_execution_cannot_bypass_preflight(self):
        task={'id':'story_test:S1','token':'test','stage':'shot','deps':[],
              'payload':{'plan':{**self.p,'input_sha256':{}},'folder':str(self.root),'data':self.p['shots'][0]}}
        with patch('workflow.runner.ssh_generate') as remote, patch('workflow.media.normalize_shot') as normalize:
            with self.assertRaisesRegex(StageError,'preflight'):execute(self.q,task)
        remote.assert_not_called();normalize.assert_not_called()

    def test_legacy_graph_has_no_new_dependency(self):
        self.p=json.loads(Path('workflow/examples/smoke_bridge.json').read_text())
        self.submit()
        self.assertFalse(any(t['stage']=='story_preflight' for t in self.q.status()))
        self.assertIsNotNone(self.q.claim(['cpu'],stages={'shot'}))

    def test_animatic_review_failure_blocks_generation(self):
        self.p['input_sha256']={}
        with patch('workflow.roughcut.render',return_value={'preview':'unused.mp4'}),\
             patch('workflow.roughcut.freeze',side_effect=lambda c:c),\
             patch('workflow.narrative_gate.review_manifest'),patch('workflow.narrative_gate.require_review'),\
             patch('workflow.media.review_contact'),\
             patch('workflow.review.editorial_review',return_value={'status':'review_required'}):
            with self.assertRaisesRegex(ValueError,'animatic needs revision'):
                storyboard.preflight(self.p,{'effective_narration':self.p['narration'],'audio_path':'voice.wav'},
                                     {'audio_path':'music.wav'},self.root)

    def test_real_voice_to_animatic_to_generation_dependency(self):
        voice=self.root/'voice.wav';bed=self.root/'bed.wav';segment=self.root/'segment.wav'
        media.ff('-f','lavfi','-i','sine=frequency=440:duration=16:sample_rate=48000',voice)
        media.ff('-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t',16,bed)
        media.ff('-f','lavfi','-i','sine=frequency=440:duration=4:sample_rate=48000',segment)
        timing=self.root/'timing.json'
        write_json(timing,{'schema_version':1,'method':'edge_word_boundary','script_text':self.p['narration'][0]['text'],
            'source_path':str(segment),'source_sha256':sha256(segment),'source_seconds':4,
            'words':[{'text':'先装货','start':0,'end':1.5},{'text':'再固定','start':2,'end':3.5}]})
        self.p['narration'][0].update(audio_path=str(segment),word_timing_path=str(timing))
        self.p['prebuilt_narration']={'path':str(voice),'effective_narration':copy.deepcopy(self.p['narration']),'segments':[]}
        self.p['prebuilt_music']={'path':str(bed),'config_sha256':digest(self.p['music'])}
        for i,s in enumerate(self.p['shots']):
            media.ff('-f','lavfi','-i',f'color={"blue" if i==0 else "red"}:s=96x160:r=24:d=8',
                     '-an','-c:v','libx264',s['source_path'])
        self.submit()
        for stage in ('voice','music'):
            task=self.q.claim(['cpu'],stages={stage});self.q.finish(task,result=execute(self.q,task))
        self.assertIsNone(self.q.claim(['cpu'],stages={'shot'}))
        task=self.q.claim(['cpu'],stages={'story_preflight'})
        from workflow.narrative_gate import CHECKS
        answer={'verdict':'pass','checks':{k:True for k in CHECKS},'issues':[],'summary':'test fixture'}
        with patch('workflow.llm.ask_json',return_value=(answer,{'model':'test','usage':{}})),\
             patch('workflow.review.editorial_review',return_value={'status':'automatic_review_passed','fixture':True}),\
             patch('workflow.runner.ssh_generate') as remote:
            result=execute(self.q,task)
        remote.assert_not_called()
        self.assertTrue(Path(result['preview_path']).is_file())
        self.assertIn(result['review_path'],result['sha256'])
        self.q.finish(task,result=result)
        self.assertIsNotNone(self.q.claim(['cpu'],stages={'shot'}))

    def test_batch_saves_new_story_without_dispatch_and_resumes_cached_plan(self):
        from workflow.batch import produce
        p=film();topic={'id':'one','status':'source_supported','title':'装货','theme':'product',
                'visual_potential':4,'source_url':p['facts'][0]['source_url'],'facts':p['facts'],'published':'2026-10-03'}
        calls=[]
        def planner(brief,path):
            calls.append(brief);return {**p,'id':brief['id']}
        config={'limit':1,'music':p['music'],'backend':{'type':'assets'}}
        first=produce(self.q,{'topics':[topic]},config,self.root,planner)
        second=produce(self.q,{'topics':[topic]},config,self.root,planner)
        self.assertEqual(first[0]['state'],'awaiting_storyboard')
        self.assertEqual(second[0]['state'],'awaiting_storyboard')
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['pacing_policy'],'storyboard_v1')
        self.assertEqual(self.q.status(),[])


class ActualAudioTests(unittest.TestCase):
    def test_real_composition_copies_exact_approved_audio(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'accepted.mp4';shot=root/'shot.mp4'
            media.ff('-f','lavfi','-i','color=blue:s=96x160:r=24:d=2',
                     '-f','lavfi','-i','sine=frequency=440:duration=2:sample_rate=48000',
                     '-c:v','libx264','-c:a','aac',source)
            media.ff('-f','lavfi','-i','color=red:s=96x160:r=24:d=2','-an','-c:v','libx264',shot)
            out=root/'out';out.mkdir()
            p={'duration_seconds':2,'title':'装货','cover_title':'试装','narration':[{'start':0,'end':1,'text':'试装。'}],
               'shots':[{'id':'one','duration_seconds':2}],
               'locked_audio':{'path':str(source),'sha256':sha256(source)}}
            result=media.compose(p,[shot],source,source,out)
            def pcm(path):
                return media.run('ffmpeg','-v','error','-i',path,'-map','0:a:0','-f','s16le','-')
            self.assertEqual(pcm(source),pcm(result['video_path']))
            receipt=json.loads(Path(result['audio_mix_path']).read_text())
            self.assertTrue(receipt['audio_stream_copied'])
            p['locked_audio']['sha256']='changed'
            with self.assertRaisesRegex(ValueError,'audio changed'):media.verify_locked_audio(p)

    def test_supplied_word_times_follow_actual_tempo(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);audio=root/'voice.mp3';audio.write_bytes(b'exact')
            timing=root/'words.json';text='先装货，再固定。'
            cue={'schema_version':1,'method':'edge_word_boundary','source_path':str(audio),
                 'source_sha256':sha256(audio),'source_seconds':4,'script_text':text,
                 'words':[{'text':'先装货','start':0,'end':1.5},{'text':'再固定','start':2,'end':3.5}]}
            write_json(timing,cue)
            p=film();p['narration'][0].update(audio_path=str(audio),word_timing_path=str(timing))
            with patch('workflow.media.duration',return_value=4),patch('workflow.media.ff'):
                result=media.narration(p,root/'mixed.wav')
            n=result['effective_narration'][0]
            self.assertAlmostEqual(n['caption_tempo'],4/3.72)
            self.assertEqual(n['text'],text)


class StoryActionEvidenceTests(unittest.TestCase):
    def test_static_sample_cannot_pass_action_and_state(self):
        from workflow.shot_review import assess, STORY_CRITERIA
        times=[round(i/6,4) for i in range(24)]
        answer={'checks':[{'criterion':c,'verdict':'pass','confidence':.95,
                  'evidence_seconds':[times[0],times[-1]],'observation':'Observed result.'} for c in STORY_CRITERIA],
                'findings':[],'not_assessed':['continuous motion']}
        self.assertEqual(assess(answer,times,STORY_CRITERIA),'sampled_checks_passed')
        answer['checks'][-1]['evidence_seconds']=[times[0]]
        with self.assertRaisesRegex(ValueError,'beginning and ending'):assess(answer,times,STORY_CRITERIA)

    def test_all_story_shots_are_reviewed_against_their_keyframes(self):
        from workflow.shot_review import review_film
        p=film()
        for s in p['shots']:s['reference_images']=['keyframe.png']
        with patch('workflow.shot_review.review_shot',return_value={'status':'sampled_checks_passed'}) as review:
            r=review_film(p,'unused','unused')
        self.assertEqual(review.call_count,2)
        self.assertEqual(r['api_calls_budget'],2)
        self.assertEqual(review.call_args.kwargs['references'],['keyframe.png'])


if __name__ == '__main__':
    unittest.main()
