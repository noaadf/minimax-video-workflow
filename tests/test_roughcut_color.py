import copy,json
from unittest.mock import patch
from tests.test_roughcut import RoughcutTests
from workflow import media
from workflow.plan import sha256
from workflow.roughcut import freeze,render

class RoughcutColorTests(RoughcutTests):
    def test_real_grade_preserves_graphic_and_precedes_subtitles(self):
        media.ff('-f','lavfi','-i','color=red:s=96x160:r=24:d=2','-c:v','libx264',self.root/'video.mp4')
        for stem in ('voice.wav','music.wav'):
            media.ff('-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t',2,self.root/stem)
        cue=json.loads((self.root/'timing.json').read_text());cue['source_sha256']=sha256(self.root/'voice.wav')
        (self.root/'timing.json').write_text(json.dumps(cue))
        config=copy.deepcopy(self.config)
        config['shots']=[dict(config['shots'][0],duration_seconds=1),dict(config['shots'][0],id='S2',duration_seconds=1,source_start=1)]
        config['caption_plan']['shots']=[{'id':'S1','duration_seconds':1,'production_mode':'generated_action'},
                                          {'id':'S2','duration_seconds':1,'production_mode':'deterministic_graphic'}]
        config['caption_plan']['color_policy']={'version':'natural_v1','saturation':.7,'contrast':1,'brightness':0}
        with patch('workflow.media.ff',wraps=media.ff) as call:
            result=render(freeze(config),self.root/'graded')
        final=[c.args for c in call.call_args_list if '-vf' in c.args and 'ass=' in c.args[c.args.index('-vf')+1]][0]
        vf=final[final.index('-vf')+1]
        self.assertLess(vf.index('eq='),vf.index('ass='))
        receipt=json.loads((self.root/'graded/color_grading.json').read_text())
        self.assertEqual([s['saturation'] for s in receipt['shots']],[.7,1])
        def pixel(t):return media.run('ffmpeg','-v','error','-ss',t,'-i',result['preview'],'-frames:v',1,'-vf','crop=2:2:268:478','-f','rawvideo','-pix_fmt','rgb24','-')[:3]
        live,graphic=pixel(.5),pixel(1.5)
        self.assertGreater(live[1],graphic[1]+10)
