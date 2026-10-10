"""film_ir_plan adapter tests: parity with the legacy constructor, no-fallback
blocking, and the frame-exact render path (real ffmpeg fixtures)."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from workflow import film_ir, film_ir_plan, runner

CFG = {'remote_root': '/remote/root', 'generation': {'steps': 50, 'short_edge': 768}}
PROMPT = '塔架细节情景演绎，低饱和光线。'
CONTINUITY = '连续性：矿灯与钢结构。'


class Refs:
    """Keyframe fixtures that survive double module collection: recreate on access
    after a previous collection's tearDownModule removed the directory."""

    def __init__(self):
        self._dir = None

    def _ensure(self):
        if self._dir is None or not os.path.isdir(self._dir.name):
            self._dir = tempfile.TemporaryDirectory()
            open(os.path.join(self._dir.name, 'kf_start.png'), 'wb').write(b'start-image-bytes')
            open(os.path.join(self._dir.name, 'kf_end.png'), 'wb').write(b'end-image-bytes')

    @property
    def start(self):
        self._ensure()
        return os.path.join(self._dir.name, 'kf_start.png')

    @property
    def end(self):
        self._ensure()
        return os.path.join(self._dir.name, 'kf_end.png')

    def cleanup(self):
        if self._dir is not None:
            self._dir.cleanup()
            self._dir = None


R = Refs()


def make_plan(durations=(6.136, 5.944, 6.112), refs=(None, None)):
    """Two reused source shots, then one generated shot S03."""
    refs = tuple(refs) if refs != (None, None) else (R.start, R.end)
    shots = [{'id': 'S01', 'duration_seconds': durations[0], 'source_path': '/asset/S01.mp4'},
             {'id': 'S02', 'duration_seconds': durations[1], 'source_path': '/asset/S02.mp4'},
             {'id': 'S03', 'duration_seconds': durations[2], 'prompt': PROMPT,
              'reference_images': list(refs), 'seed': 5}]
    return {'id': 'production_test', 'pacing_policy': 'storyboard_v1',
            'visual_continuity': CONTINUITY, 'shots': shots}


TASK = {'id': 'production_test:S03'}


def compile_plan(plan, cfg=CFG, contract=None):
    return film_ir_plan.compile_shot_from_plan(plan, plan['shots'][2], cfg, contract)


def tearDownModule():
    R.cleanup()


class AdapterMappingTest(unittest.TestCase):
    def test_slot_start_is_cumulative_including_source_shots(self):
        manifest = compile_plan(make_plan())
        acc = manifest['shots'][0]['accounting']
        self.assertEqual(acc['slot_frames'], film_ir.frames(6.136 + 5.944 + 6.112)
                         - film_ir.frames(6.136 + 5.944))

    def test_duration_tier_matches_legacy_formula(self):
        manifest = compile_plan(make_plan())
        request = manifest['shots'][0]['segments'][0]['request']
        legacy_seconds = max(4, -(-6.112 // 1))  # ceil
        self.assertEqual(request['duration_seconds'], legacy_seconds)
        # native frames = MEASURED backend count for the tier, not seconds*24
        self.assertEqual(manifest['profile']['frames'],
                         film_ir.native_frames(legacy_seconds))

    def test_native_frames_law_reproduces_every_measured_tier(self):
        for seconds, frames in film_ir.MEASURED_NATIVE_FRAMES.items():
            self.assertEqual(film_ir.native_frames_law(seconds), frames, f'{seconds}s tier')
            self.assertEqual(frames % 17, 5)  # backend quantization invariant

    def test_compile_feasibility_uses_measured_native_frames(self):
        # 2.7s slot -> 4s tier: backend emits 107 frames (not 96). 64-71f slots must
        # be judged infeasible at COMPILE time, never after burning NPU time.
        plan = make_plan(durations=(6.0, 6.0, 2.7))
        manifest = compile_plan(plan)
        kinds = {v['kind'] for v in manifest['violations']}
        self.assertIn('slot_infeasible', kinds)

    def test_conditioning_comes_from_ref_count(self):
        self.assertEqual(compile_plan(make_plan())['shots'][0]['conditioning'], 'first_last')
        m1 = compile_plan(make_plan(refs=(R.start,)))
        self.assertEqual(m1['shots'][0]['conditioning'], 'first_only')
        self.assertEqual(len(m1['shots'][0]['segments'][0]['request']['reference_images']), 1)

    def test_prompt_is_verbatim_authored(self):
        request = compile_plan(make_plan())['shots'][0]['segments'][0]['request']
        self.assertEqual(request['prompt'], CONTINUITY + '\n' + PROMPT)

    def test_v3_binding_appends_suffix_and_rekeys(self):
        base = compile_plan(make_plan())
        bound = compile_plan(make_plan(), contract='v3_binding')
        prompt = bound['shots'][0]['segments'][0]['request']['prompt']
        self.assertTrue(prompt.startswith(CONTINUITY + '\n' + PROMPT))
        self.assertIn('end exactly at the last reference frame', prompt)
        self.assertNotEqual(base['shots'][0]['segments'][0]['id'],
                            bound['shots'][0]['segments'][0]['id'])

    def test_unknown_pipeline_rejected(self):
        with self.assertRaises(ValueError):
            film_ir_plan.pipeline_of({'shot_pipeline': 'mystery'})


class ParityTest(unittest.TestCase):
    def test_compiled_request_matches_legacy_field_by_field(self):
        plan = make_plan()
        legacy_remote, legacy = runner.visual_generation_request(plan, plan['shots'][2], TASK, CFG)
        remote, request, manifest = film_ir_plan.compiled_request(plan, plan['shots'][2], TASK, CFG)
        for field in ('task_id', 'prompt', 'duration_seconds', 'aspect_ratio', 'seed', 'generation'):
            self.assertEqual(legacy[field], request[field], field)
        expected_legacy_refs = [legacy_remote + f'/reference_{i}{Path(r).suffix}'
                                for i, r in enumerate(plan['shots'][2]['reference_images'])]
        expected_ir_refs = [remote + f'/reference_{i}{Path(r).suffix}'
                            for i, r in enumerate(request['reference_images'])]
        self.assertEqual([Path(r).name for r in expected_legacy_refs],
                         [Path(r).name for r in expected_ir_refs])
        self.assertTrue(remote.startswith(CFG['remote_root'] + '/tasks/'))
        self.assertNotEqual(remote, legacy_remote)  # segment-bound remote root

    def test_shadow_report_empty_on_healthy_plan(self):
        plan = make_plan()
        legacy_remote, legacy = runner.visual_generation_request(plan, plan['shots'][2], TASK, CFG)
        report = film_ir_plan.shadow_report(plan, plan['shots'][2], TASK, CFG, legacy_remote, legacy)
        self.assertEqual(report['diffs'], [])

    def test_shadow_report_catches_drift(self):
        plan = make_plan()
        legacy_remote, legacy = runner.visual_generation_request(plan, plan['shots'][2], TASK, CFG)
        legacy = dict(legacy, duration_seconds=legacy['duration_seconds'] + 1)
        report = film_ir_plan.shadow_report(plan, plan['shots'][2], TASK, CFG, legacy_remote, legacy)
        self.assertTrue(any(d['field'] == 'duration_seconds' for d in report['diffs']))


class NoFallbackTest(unittest.TestCase):
    def test_missing_kf_file_raises_instead_of_legacy_dict(self):
        plan = make_plan(refs=('/nonexistent/kf.png', R.end))
        with self.assertRaises(ValueError) as ctx:
            film_ir_plan.compiled_request(plan, plan['shots'][2], TASK, CFG)
        self.assertIn('missing_kf_file', str(ctx.exception))

    def test_three_or_more_refs_rejected_not_truncated(self):
        plan = make_plan(refs=(R.start, R.end, R.start))
        with self.assertRaises(ValueError) as ctx:
            compile_plan(plan)
        self.assertIn('3 reference images', str(ctx.exception))

    def test_shadow_entry_records_failure_without_raising(self):
        # Shadow mode must be side-effect-free on the legacy path: a t2va shot
        # (no keyframes) is recorded, and the legacy request still submits.
        plan = make_plan(refs=())
        entry = film_ir_plan.shadow_entry(plan, plan['shots'][2], TASK, CFG,
                                          '/remote/root/tasks/x', {'task_id': 't'})
        self.assertIn('shadow_failed', entry)
        self.assertIn('missing_start_kf', entry['shadow_failed'])

    def test_none_ref_entry_rejected_not_reinterpreted(self):
        plan = make_plan(refs=(R.start, R.end))
        plan['shots'][2]['reference_images'] = [R.start, None]
        with self.assertRaises(ValueError) as ctx:
            compile_plan(plan)
        self.assertIn('non-string reference_images', str(ctx.exception))

    def test_infeasible_slot_raises(self):
        plan = make_plan(durations=(6.0, 6.0, 1.2))  # tier 4s vs 1.2s slot -> 0.3x
        with self.assertRaises(ValueError) as ctx:
            film_ir_plan.compiled_request(plan, plan['shots'][2], TASK, CFG)
        self.assertIn('slot_infeasible', str(ctx.exception))

    def test_empty_prompt_rejected_like_legacy(self):
        plan = make_plan()
        plan['shots'][2]['prompt'] = '   '
        with self.assertRaises(ValueError):
            compile_plan(plan)


class RenderPathTest(unittest.TestCase):
    """Real ffmpeg: terminal-frame preservation and integer-slot closure through
    render_compiled_shot (the assembly-chain coverage the POC review demanded)."""

    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls._dir.name)
        # native 5.000s = 120 frames, canvas 768x1344 @ 24fps
        cls.native = cls.root / 'native.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                        '-i', 'testsrc=duration=5:size=768x1344:rate=24',
                        '-pix_fmt', 'yuv420p', str(cls.native)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def _render(self, slot, name):
        plan = make_plan(durations=(6.0, 6.0, slot))
        return plan, film_ir_plan.render_compiled_shot(plan, plan['shots'][2],
                                                       str(self.native), str(self.root / name))

    def test_identity_rate_preserves_terminal_frame(self):
        _, info = self._render(5.0, 'a.mp4')
        self.assertEqual(info['slot_frames'], 120)
        self.assertEqual(info['produced_frames'], 120)
        self.assertEqual(info['playback_rate'], 1.0)
        self.assertEqual(info['settle_warnings'], [])

    def test_retimed_slot_is_frame_exact(self):
        _, info = self._render(4.5, 'b.mp4')
        self.assertEqual(info['slot_frames'], 108)   # frames(4.5)
        self.assertEqual(info['produced_frames'], 108)
        self.assertAlmostEqual(info['playback_rate'], 108 / 120, places=5)

    def test_out_of_bound_measured_rejected(self):
        with self.assertRaises(ValueError):
            self._render(2.0, 'c.mp4')

    def test_render_binds_to_dispatched_segment(self):
        plan = make_plan()
        _, _, manifest = film_ir_plan.compiled_request(plan, plan['shots'][2], TASK, CFG)
        tampered = json.loads(json.dumps(manifest))
        tampered['shots'][0]['segments'][0]['id'] = 'deadbeefdeadbeef'
        with self.assertRaises(ValueError) as ctx:
            film_ir_plan.render_compiled_shot(plan, plan['shots'][2],
                                              str(self.native), '/tmp/bound.mp4', submitted=tampered)
        self.assertIn('segment identity changed', str(ctx.exception))

class SourceShotTest(unittest.TestCase):
    """Integer-slot rendering for reused assets: the mixed-timeline closure fix."""

    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls._dir.name)
        # exact-contract asset: 1080x1920 h264 24fps, 147 frames (6.125s)
        cls.asset = cls.root / 'asset.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                        '-i', 'testsrc=duration=6.125:size=1080x1920:rate=24',
                        '-pix_fmt', 'yuv420p', str(cls.asset)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def _plan_shot(self, dur):
        plan = make_plan(durations=(dur, 1.0, 1.0))
        plan['shots'] = [plan['shots'][0]]
        plan['shots'][0]['source_path'] = str(self.asset)
        plan['shots'][0]['prompt'] = PROMPT
        plan['shots'][0]['reference_images'] = [R.start, R.end]
        return plan

    def test_exact_asset_byte_preserved(self):
        plan = self._plan_shot(6.125)  # slot = frames(6.125) = 147 == asset frames
        info = film_ir_plan.render_source_shot(plan, plan['shots'][0], str(self.asset),
                                               str(self.root / 'p1.mp4'))
        self.assertEqual(info['source'], 'reused_asset_byte_preserved')
        self.assertEqual(info['slot_frames'], 147)
        self.assertEqual(Path(plan['shots'][0] and (self.root / 'p1.mp4')).read_bytes(),
                         self.asset.read_bytes())

    def test_longer_asset_retimed_to_integer_slot(self):
        plan = self._plan_shot(6.0)  # slot = 144; legacy -t would emit 144 via float, assert exact
        info = film_ir_plan.render_source_shot(plan, plan['shots'][0], str(self.asset),
                                               str(self.root / 'p2.mp4'))
        self.assertEqual(info['source'], 'reused_asset_retimed')
        self.assertEqual(info['produced_frames'], 144)
        self.assertEqual(info['slot_frames'], 144)

    def test_short_source_rejected(self):
        plan = self._plan_shot(8.0)  # slot 192 > asset 147 frames
        with self.assertRaises(ValueError) as ctx:
            film_ir_plan.render_source_shot(plan, plan['shots'][0], str(self.asset),
                                            str(self.root / 'p3.mp4'))
        self.assertIn('6.125s < slot 8.000s', str(ctx.exception))

    def test_whole_action_reuse_speed_ramps_into_slot(self):
        # repair-round intent: the COMPLETE clip retimes into the reflowed slot
        plan = self._plan_shot(6.5)  # slot 156; asset 147f -> playback 156/147=1.061
        plan['shots'][0]['source_fit_policy'] = 'whole_action'
        info = film_ir_plan.render_source_shot(plan, plan['shots'][0], str(self.asset),
                                               str(self.root / 'p4.mp4'))
        self.assertEqual(info['source'], 'reused_asset_speed_ramped')
        self.assertEqual(info['produced_frames'], 156)
        self.assertEqual(info['slot_frames'], 156)

    def test_whole_action_out_of_bound_rejected(self):
        plan = self._plan_shot(9.5)  # slot 228 vs 147f -> 1.551x beyond 1.5
        plan['shots'][0]['source_fit_policy'] = 'whole_action'
        with self.assertRaises(ValueError) as ctx:
            film_ir_plan.render_source_shot(plan, plan['shots'][0], str(self.asset),
                                            str(self.root / 'p5.mp4'))
        self.assertIn('exceeds', str(ctx.exception))

    def test_low_fps_source_fills_slot_via_fps_filter(self):
        # astra r2 E: 12fps/48f asset legitimately fills a 96f/24fps slot; the
        # old frame-count precheck wrongly rejected it. Feasibility is by SECONDS.
        slow = self.root / 'slow12.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                        '-i', 'testsrc=duration=4:size=1080x1920:rate=12',
                        '-pix_fmt', 'yuv420p', str(slow)], check=True)
        plan = self._plan_shot(4.0)  # slot 96
        plan['shots'][0]['source_path'] = str(slow)
        info = film_ir_plan.render_source_shot(plan, plan['shots'][0], str(slow),
                                               str(self.root / 'p7.mp4'))
        self.assertEqual(info['slot_frames'], 96)
        self.assertEqual(info['produced_frames'], 96)

    def test_high_fps_whole_action_uses_duration_ratio(self):
        # astra r2 E: 30fps/120f whole_action must scale by duration ratio, not
        # slot/measured frames (96/120 mixed timebases and produced 77f).
        fast = self.root / 'fast30.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                        '-i', 'testsrc=duration=4:size=1080x1920:rate=30',
                        '-pix_fmt', 'yuv420p', str(fast)], check=True)
        plan = self._plan_shot(4.0)  # slot 96; duration ratio 4/4 = 1.0
        plan['shots'][0]['source_fit_policy'] = 'whole_action'
        plan['shots'][0]['source_path'] = str(fast)
        info = film_ir_plan.render_source_shot(plan, plan['shots'][0], str(fast),
                                               str(self.root / 'p8.mp4'))
        self.assertEqual(info['source'], 'reused_asset_speed_ramped')
        self.assertEqual(info['produced_frames'], 96)

    def test_high_fps_whole_action_with_real_speedup(self):
        # opus r3: the 30fps case ran at 1.0x; exercise an actual setpts scale
        # (30fps 5s source -> 4s slot, playback 0.8) and require exact frames.
        fast = self.root / 'fast30b.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                        '-i', 'testsrc=duration=5:size=1080x1920:rate=30',
                        '-pix_fmt', 'yuv420p', str(fast)], check=True)
        plan = self._plan_shot(4.0)  # slot 96 vs 5.0s source -> playback 0.8
        plan['shots'][0]['source_fit_policy'] = 'whole_action'
        plan['shots'][0]['source_path'] = str(fast)
        info = film_ir_plan.render_source_shot(plan, plan['shots'][0], str(fast),
                                               str(self.root / 'p10.mp4'))
        self.assertEqual(info['produced_frames'], 96)
        self.assertEqual(info['slot_frames'], 96)

    def test_stage_reference_indexes_and_binds_content(self):
        import hashlib as _h
        staged_dir = self.root / 'refs'
        sha = _h.sha256(open(R.start, 'rb').read()).hexdigest()
        a = film_ir_plan.stage_reference(R.start, sha, staged_dir, 0)
        b = film_ir_plan.stage_reference(R.end, _h.sha256(open(R.end, 'rb').read()).hexdigest(),
                                         staged_dir, 1)
        self.assertNotEqual(a, b)  # same-suffix refs must not overwrite each other
        self.assertTrue(Path(a).is_file() and Path(b).is_file())
        with self.assertRaises(ValueError) as ctx:
            film_ir_plan.stage_reference(R.end, sha, staged_dir, 2)  # content mismatch
        self.assertIn('content changed since compile', str(ctx.exception))

    def test_coarse_time_base_whole_action_exact(self):
        # astra r3: 12fps source at tb 1/12 — setpts truncates sub-tick, 97f slot
        # delivered 96. settb=AVTB must make it exact.
        coarse = self.root / 'coarse12.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                        '-i', 'testsrc=duration=4:size=1080x1920:rate=12',
                        '-video_track_timescale', '12', '-pix_fmt', 'yuv420p',
                        str(coarse)], check=True)
        plan = self._plan_shot(97 / 24)  # slot 97, playback 1.0104
        plan['shots'][0]['source_fit_policy'] = 'whole_action'
        plan['shots'][0]['source_path'] = str(coarse)
        info = film_ir_plan.render_source_shot(plan, plan['shots'][0], str(coarse),
                                               str(self.root / 'p11.mp4'))
        self.assertEqual(info['slot_frames'], 97)
        self.assertEqual(info['produced_frames'], 97)

    def test_container_duration_mismatch_blocks_byte_preserve(self):
        # stream duration 1s but container 2s: the check must read the CONTAINER.
        from unittest import mock
        asset = self.root / 'mismatch.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                        '-i', 'testsrc=duration=1:size=1080x1920:rate=24',
                        '-pix_fmt', 'yuv420p', str(asset)], check=True)
        plan = self._plan_shot(1.0)  # slot 24 == nb_frames
        plan['shots'][0]['source_path'] = str(asset)
        crafted = {'streams': [{'codec_type': 'video', 'nb_frames': '24', 'duration': '1.0',
                                'width': 1080, 'height': 1920, 'r_frame_rate': '24/1',
                                'avg_frame_rate': '24/1', 'time_base': '1/12288',
                                'codec_name': 'h264', 'pix_fmt': 'yuv420p',
                                'sample_aspect_ratio': '1:1'}],
                   'format': {'duration': '2.0', 'start_time': '0.00'}}
        with mock.patch('workflow.media.probe', return_value=crafted):
            info = film_ir_plan.render_source_shot(plan, plan['shots'][0], str(asset),
                                                   str(self.root / 'p12.mp4'))
        self.assertNotEqual(info['source'], 'reused_asset_byte_preserved')

    def test_no_video_stream_rejected_cleanly(self):
        audio_only = self.root / 'audio.m4a'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                        '-i', 'sine=duration=1', str(audio_only)], check=True)
        with self.assertRaises(ValueError) as ctx:
            film_ir_plan._probe_video(str(audio_only))
        self.assertIn('no video stream', str(ctx.exception))

    def test_noncanonical_time_base_not_byte_preserved(self):
        # astra r2 E: conforming CFR assets with time_base 1/24000 concatenated
        # to 94 frames instead of 48. Only the pipeline-canonical 1/12288 rides.
        odd_tb = self.root / 'odd_tb.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                        '-i', 'testsrc=duration=1:size=1080x1920:rate=24',
                        '-video_track_timescale', '24000', '-pix_fmt', 'yuv420p',
                        str(odd_tb)], check=True)
        plan = self._plan_shot(1.0)  # slot 24 == nb_frames
        plan['shots'][0]['source_path'] = str(odd_tb)
        info = film_ir_plan.render_source_shot(plan, plan['shots'][0], str(odd_tb),
                                               str(self.root / 'p9.mp4'))
        self.assertNotEqual(info['source'], 'reused_asset_byte_preserved')
        self.assertEqual(info['produced_frames'], 24)

    def test_vfr_asset_not_byte_preserved(self):
        # r_frame_rate stays 24/1 on CFR-timestamped VFR; only avg_frame_rate betrays
        # it (opus E4). Such an asset must re-encode, not ride into the timeline.
        vfr = self.root / 'vfr.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                        '-i', 'testsrc=duration=5:size=1080x1920:rate=24',
                        '-vf', 'setpts=PTS*139/2880*24', '-pix_fmt', 'yuv420p', str(vfr)],
                       check=True)
        plan = self._plan_shot(5.0)  # slot 120
        plan['shots'][0]['source_path'] = str(vfr)
        info = film_ir_plan.render_source_shot(plan, plan['shots'][0], str(vfr),
                                               str(self.root / 'p6.mp4'))
        self.assertNotEqual(info['source'], 'reused_asset_byte_preserved')
        self.assertEqual(info['produced_frames'], 120)


if __name__ == '__main__':
    unittest.main()
