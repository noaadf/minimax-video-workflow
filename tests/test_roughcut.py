import array
import copy
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import media
from workflow.plan import sha256
from workflow.roughcut import freeze, render, validate


class RoughcutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("video.mp4", "voice.wav", "music.wav"):
            (self.root / name).write_bytes(name.encode())
        cue = {"schema_version": 1, "method": "edge_word_boundary", "script_text": "你好。",
               "source_path": str(self.root / "voice.wav"), "source_sha256": sha256(self.root / "voice.wav"),
               "source_seconds": 2, "words": [{"start": .1, "end": 1, "text": "你好"}]}
        (self.root / "timing.json").write_text(json.dumps(cue))
        self.config = {"schema_version": 1, "kind": "editorial_roughcut", "duration_seconds": 2,
                       "narration_path": str(self.root / "voice.wav"), "music_path": str(self.root / "music.wav"),
                       "shots": [{"id": "S1", "source_path": str(self.root / "video.mp4"), "duration_seconds": 2,
                                  "source_start": 0, "purpose": "show the result"}],
                       "caption_plan": {"duration_seconds": 2, "caption_policy": "speech_aligned_v1",
                           "narration": [{"start": 0, "end": 1.8, "text": "你好。",
                                          "word_timing_path": str(self.root / "timing.json")}]}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_inputs_are_bound_and_stale_assets_rejected(self):
        config = freeze(self.config)
        self.assertEqual(len(config["input_sha256"]), 4)
        (self.root / "video.mp4").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "input changed"):
            validate(config)

    def test_empty_purpose_bad_sum_and_missing_hash_rejected(self):
        for edit in [lambda c: c["shots"][0].update(purpose=""),
                     lambda c: c["shots"][0].update(duration_seconds=1.5),
                     lambda c: c.update(input_sha256={})]:
            config = freeze(self.config)
            edit(config)
            with self.assertRaises(ValueError):
                validate(config)

    def test_short_video_refuses_padding(self):
        config = freeze(self.config)
        with patch("workflow.media.duration", side_effect=lambda path: 1 if str(path).endswith("mp4") else 2):
            with self.assertRaisesRegex(ValueError, "source too short"):
                validate(config)

    def test_whole_action_preserves_end_state_in_slot_and_animatic(self):
        source=self.root/'video.mp4'
        media.ff('-f','lavfi','-i','color=blue:s=96x160:r=24:d=2.15',
                 '-f','lavfi','-i','color=red:s=96x160:r=24:d=0.35',
                 '-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]',
                 '-c:v','libx264','-an',source)
        cropped=self.root/'cropped.mp4';whole=self.root/'whole.mp4'
        media.normalize_shot(source,cropped,2)
        media.normalize_shot(source,whole,2,fit_policy='whole_action')
        def pixel(path,t):
            return media.run('ffmpeg','-v','error','-ss',t,'-i',path,'-frames:v',1,
                '-vf','scale=2:2','-f','rawvideo','-pix_fmt','rgb24','-')[:3]
        a=pixel(cropped,1.9);b=pixel(whole,1.9)
        self.assertGreater(a[2],a[0]+100)
        self.assertGreater(b[0],b[2]+100)
        for stem in ('voice.wav','music.wav'):
            media.ff('-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t',2,self.root/stem)
        cue=json.loads((self.root/'timing.json').read_text());cue['source_sha256']=sha256(self.root/'voice.wav')
        (self.root/'timing.json').write_text(json.dumps(cue))
        config=copy.deepcopy(self.config);config['shots'][0]['source_fit_policy']='whole_action'
        report=render(freeze(config),self.root/'whole_animatic')
        color=pixel(report['preview'],1.9);self.assertGreater(color[0],color[2]+100)
        self.assertAlmostEqual(media.duration(whole),2,places=1)

    def test_extreme_whole_action_speed_requires_retiming(self):
        source=self.root/'long.mp4';media.ff('-f','lavfi','-i','color=blue:s=96x160:r=24:d=4',
            '-c:v','libx264','-an',source)
        with self.assertRaisesRegex(ValueError,'retime'):media.whole_action_filter(source,1)
        with self.assertRaisesRegex(ValueError,'from zero'):media.whole_action_filter(source,4,start=.1)

    def test_real_start_end_keyframes_and_locked_audio(self):
        source=self.root/'accepted.mp4';start=self.root/'start.png';end=self.root/'end.png'
        media.ff('-f','lavfi','-i','color=blue:s=96x160:r=24:d=2',
                 '-f','lavfi','-i','sine=frequency=440:duration=2:sample_rate=48000',
                 '-c:v','libx264','-c:a','aac',source)
        media.ff('-f','lavfi','-i','color=blue:s=96x160','-frames:v',1,start)
        media.ff('-f','lavfi','-i','color=red:s=96x160','-frames:v',1,end)
        for stem in ('voice.wav','music.wav'):
            media.ff('-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t',2,self.root/stem)
        cue=json.loads((self.root/'timing.json').read_text());cue['source_sha256']=sha256(self.root/'voice.wav')
        (self.root/'timing.json').write_text(json.dumps(cue))
        config=copy.deepcopy(self.config)
        config['shots'][0].update(kind='image',source_path=str(start),end_source_path=str(end))
        config['locked_audio']={'path':str(source),'sha256':sha256(source)}
        report=render(freeze(config),self.root/'start_end')
        def pixel(t):
            return media.run('ffmpeg','-v','error','-ss',t,'-i',report['preview'],'-frames:v',1,
                             '-vf','crop=2:2:268:478','-f','rawvideo','-pix_fmt','rgb24','-')[:3]
        a=pixel(.2);b=pixel(1.5)
        self.assertGreater(a[2],a[0]+100);self.assertGreater(b[0],b[2]+100)
        def pcm(path):return media.run('ffmpeg','-v','error','-i',path,'-map','0:a:0','-f','s16le','-')
        self.assertEqual(pcm(source),pcm(report['preview']))

    def test_review_basis_and_music_gain_are_validated_before_render(self):
        basis = self.root / "manifest.json"
        basis.write_text("{}");self.config["basis"] = {"source_manifest": str(basis)}
        config = freeze(self.config)
        basis.write_text('{"changed":true}')
        with self.assertRaisesRegex(ValueError, "input changed"):
            validate(config)
        config = freeze(self.config);config["music_gain"] = float("nan")
        with self.assertRaisesRegex(ValueError, "music_gain"):
            validate(config)

    @unittest.skipUnless(shutil.which("ffmpeg"), "requires ffmpeg")
    def test_real_render_excludes_raw_source_audio_and_keeps_pending_review(self):
        video, voice, music = (self.root / name for name in ("video.mp4", "voice.wav", "music.wav"))
        media.ff("-f", "lavfi", "-i", "color=c=red:s=240x320:r=24:d=2",
                 "-f", "lavfi", "-i", "sine=frequency=900:duration=2", "-c:v", "libx264",
                 "-c:a", "aac", "-shortest", video)
        media.ff("-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000", voice)
        media.ff("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", 2, music)
        cue = json.loads((self.root / "timing.json").read_text())
        cue["source_sha256"] = sha256(voice)
        (self.root / "timing.json").write_text(json.dumps(cue))
        report = render(freeze(self.config), self.root / "preview")
        self.assertFalse(report["approved"])
        self.assertFalse(report["source_audio_in_mix"])
        self.assertAlmostEqual(report["duration_seconds"], 2, places=1)
        raw = media.run("ffmpeg", "-v", "error", "-ss", .2, "-i", report["preview"], "-t", .5,
                        "-vn", "-ar", "16000", "-ac", "1", "-f", "f32le", "-")
        samples = array.array("f", raw)
        def magnitude(frequency):
            real = sum(x * math.cos(2*math.pi*frequency*i/16000) for i, x in enumerate(samples))
            imag = sum(x * math.sin(2*math.pi*frequency*i/16000) for i, x in enumerate(samples))
            return math.hypot(real, imag)
        self.assertGreater(magnitude(440), magnitude(900) * 20)
        review = json.loads((self.root / "preview/review.json").read_text())
        self.assertEqual(review["decision"], "pending")
        with self.assertRaisesRegex(ValueError, "output exists"):
            render(freeze(self.config), self.root / "preview")
