import os
import tempfile
import unittest
from workflow import film_ir


class KfFiles:
    """Real keyframe files: segment identity binds image CONTENT, so fixtures need bytes."""

    def __init__(self):
        self._dir = tempfile.TemporaryDirectory()
        self.start = os.path.join(self._dir.name, 'kf_start.png')
        self.end = os.path.join(self._dir.name, 'kf_end.png')
        open(self.start, 'wb').write(b'start-image-bytes')
        open(self.end, 'wb').write(b'end-image-bytes')

    def cleanup(self):
        self._dir.cleanup()


KFS = KfFiles()


def shot(sid, slot, kfs=None, motion=None):
    start, end = kfs or (KFS.start, KFS.end)
    return {'id': sid, 'slot_start': 0.0, 'slot_seconds': slot,
            'keyframes': {'start': [start], 'end': [end]},
            'motion': motion or {'camera': {'type': 'push', 'rate': 'slow', 'amplitude': 'M'},
                                 'subject': {'action': 'the scanner sweeps across the skull'}},
            'seed': 7}


def tearDownModule():
    KFS.cleanup()


PROFILE = {'name': 'h3-fl2va-108f', 'frames': 108, 'steps': 50, 'short_edge': 768}


class FilmIRTest(unittest.TestCase):
    def test_compile_first_last(self):
        s = shot('S01', 4.4)
        s['conditioning'] = 'first_last'  # authored per v3 C3
        m = film_ir.compile_film({'film_id': 'f', 'shots': [s]}, PROFILE)
        self.assertEqual(m['violations'], [])
        c = m['shots'][0]
        self.assertEqual(c['conditioning'], 'first_last')
        self.assertEqual(len(c['segments'][0]['request']['reference_images']), 2)
        self.assertIn('end exactly at the last reference frame', c['segments'][0]['request']['prompt'])

    def test_default_conditioning_is_first_only(self):
        # v3 C3: both keyframes present but no terminal constraint -> first_only, no auto-upgrade.
        m = film_ir.compile_film({'film_id': 'f', 'shots': [shot('S01', 4.4)]}, PROFILE)
        self.assertEqual(m['shots'][0]['conditioning'], 'first_only')
        self.assertNotIn('last reference frame', m['shots'][0]['segments'][0]['request']['prompt'])
        s2 = shot('S02', 4.4)
        s2['must_show'] = ['final landmark touched']
        m2 = film_ir.compile_film({'film_id': 'f', 'shots': [s2]}, PROFILE)
        self.assertEqual(m2['shots'][0]['conditioning'], 'first_last')

    def test_seed_changes_segment_identity(self):
        a = film_ir.compile_film({'film_id': 'f', 'shots': [shot('S01', 4.4)]}, PROFILE)
        s = shot('S01', 4.4)
        s['seed'] = 99
        b = film_ir.compile_film({'film_id': 'f', 'shots': [s]}, PROFILE)
        self.assertNotEqual(a['shots'][0]['segments'][0]['id'], b['shots'][0]['segments'][0]['id'])

    def test_slot_infeasible_reported_not_stretched(self):
        m = film_ir.compile_film({'film_id': 'f', 'shots': [shot('S01', 1.8)]}, PROFILE)
        self.assertTrue(any(v['kind'] == 'slot_infeasible' for v in m['violations']))

    def test_missing_end_kf_falls_back_with_violation(self):
        s = shot('S02', 4.4)
        s['keyframes']['end'] = [None]  # authoring slip counts as missing
        s['conditioning'] = 'first_last'  # explicit request that cannot be honored
        m = film_ir.compile_film({'film_id': 'f', 'shots': [s]}, PROFILE)
        self.assertTrue(any(v['kind'] == 'missing_end_kf' for v in m['violations']))
        self.assertEqual(m['shots'][0]['conditioning'], 'first_only')
        self.assertEqual(len(m['shots'][0]['segments'][0]['request']['reference_images']), 1)

    def test_accounting_closes(self):
        m = film_ir.compile_film({'film_id': 'f', 'shots': [shot('S01', 4.4)]}, PROFILE)
        acc = m['shots'][0]['accounting']
        self.assertEqual(acc['final_frames'], film_ir.frames(4.4))
        self.assertEqual(acc['final_frames'], acc['native_frames'] - acc['dropped'] + acc['inserted_net'])

    def test_playback_within_bounds(self):
        m = film_ir.compile_film({'film_id': 'f', 'shots': [shot('S01', 5.5)]}, PROFILE)
        edit = m['shots'][0]['segments'][0]['edit']
        self.assertLessEqual(edit['playback_rate'], 1.5)
        self.assertGreaterEqual(edit['playback_rate'], 1 / 1.5)

    def test_dry_run_lists_generations(self):
        m = film_ir.compile_film({'film_id': 'f', 'shots': [shot('S01', 4.4)]}, PROFILE)
        report = film_ir.dry_run(m)
        self.assertIn('GEN S01-', report)
        self.assertEqual(m['violations'], [])

    def test_continuity_bound_missing_end_blocks(self):
        s = shot('S03', 4.4, kfs=('a.png', None))
        s['keyframes']['end'] = []
        s['conditioning'] = 'first_last'
        s['continuity_bound'] = True
        m = film_ir.compile_film({'film_id': 'f', 'shots': [s]}, PROFILE)
        self.assertTrue(any(v['kind'] == 'missing_end_kf_blocked' for v in m['violations']))
        self.assertEqual(m['shots'], [])  # excluded, not degraded

    def test_settle_accounting_uses_measured_frames(self):
        m = film_ir.compile_film({'film_id': 'f', 'shots': [shot('S01', 4.4)]}, PROFILE)
        edit, warnings = film_ir.settle_accounting(m['shots'][0], 107)  # exact nb_frames
        acc = m['shots'][0]['accounting']
        self.assertEqual(acc['native_frames'], 107)
        self.assertAlmostEqual(edit['playback_rate'], 105 / 107, places=5)
        self.assertEqual(edit['source_range'], [0, 107])
        self.assertEqual(warnings, [])  # 0.981x is inside ±10%

    def test_settle_tier_warnings(self):
        m = film_ir.compile_film({'film_id': 'f', 'shots': [shot('S01', 4.4)]}, PROFILE)
        edit, warnings = film_ir.settle_accounting(m['shots'][0], 124)  # 105/124 = 0.847x tight tier
        self.assertTrue(any(w['kind'] == 'slot_tight_warning' for w in warnings))
        self.assertTrue(edit['foley']['keep'])
        edit_r, warnings_r = film_ir.settle_accounting(m['shots'][0], 135)  # 0.778x retune
        self.assertTrue(any(w['kind'] == 'slot_retune_warning' for w in warnings_r))
        # audio rate at 0.778x playback = 1.286x -> beyond ±0.25, foley dropped
        self.assertFalse(edit_r['foley']['keep'])

    def test_settle_rejects_before_mutating(self):
        m = film_ir.compile_film({'film_id': 'f', 'shots': [shot('S01', 4.4)]}, PROFILE)
        before = dict(m['shots'][0]['accounting'])
        before_edit = dict(m['shots'][0]['segments'][0]['edit'])
        with self.assertRaises(ValueError):
            film_ir.settle_accounting(m['shots'][0], 400)  # playback out of hard bound
        self.assertEqual(m['shots'][0]['accounting'], before)  # ledger stays clean
        self.assertEqual(m['shots'][0]['segments'][0]['edit'], before_edit)

    def test_artifact_frames_prefers_exact_count(self):
        self.assertEqual(film_ir.artifact_frames(107, 4.458333), 107)   # nb_frames wins
        self.assertEqual(film_ir.artifact_frames(None, 4.458333), 107)  # round, not floor(106)

    def test_same_path_new_image_rekeys(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, 'kf.png'); open(a, 'wb').write(b'img-A')
            s1 = shot('S01', 4.4, kfs=(a, a))
            m1 = film_ir.compile_film({'film_id': 'f', 'shots': [s1]}, PROFILE)
            open(a, 'wb').write(b'img-B-different-bytes')  # same path, new content
            m2 = film_ir.compile_film({'film_id': 'f', 'shots': [shot('S01', 4.4, kfs=(a, a))]}, PROFILE)
            self.assertNotEqual(m1['shots'][0]['segments'][0]['id'], m2['shots'][0]['segments'][0]['id'])

    def test_feasibility_tiers(self):
        m = film_ir.compile_film({'film_id': 'f', 'shots': [shot('S01', 6.2)]}, PROFILE)
        kinds = {v['kind'] for v in m['violations']}
        self.assertIn('slot_retune_warning', kinds)  # 108f vs 149f -> ~1.38x
        m2 = film_ir.compile_film({'film_id': 'f', 'shots': [shot('S01', 1.8)]}, PROFILE)
        self.assertIn('slot_infeasible', {v['kind'] for v in m2['violations']})

    def test_recipe_hash_binds_backend_fingerprint(self):
        a = film_ir.recipe_hash({'prompt': 'x', 'seed': 1}, ['sha_a'], 'base-50@group2')
        b = film_ir.recipe_hash({'prompt': 'x', 'seed': 1}, ['sha_a'], 'turbo-4@group1')
        c = film_ir.recipe_hash({'prompt': 'x', 'seed': 1}, ['sha_a'], 'base-50@group2')
        self.assertNotEqual(a, b)
        self.assertEqual(a, c)


if __name__ == '__main__':
    unittest.main()


class WritingLayerLintTest(unittest.TestCase):
    """docs/h3_writing_layer.md: golden corpus must lint clean; pre-writing-layer
    prompts must warn (the documented gap the next film's hand-authoring closes)."""

    def test_golden_corpus_lints_clean(self):
        from pathlib import Path
        import re as _re
        from workflow.film_ir import writing_layer_problems
        golden = sorted(Path('research/official_ir_golden').glob('*_*.txt'))
        if not golden:
            self.skipTest('no golden artifacts present')
        for f in golden:
            with self.subTest(golden=f.name):
                self.assertEqual(writing_layer_problems(f.read_text()), [])

    def test_old_style_prompt_warns_on_all_three(self):
        from workflow.film_ir import writing_layer_problems
        prompt = ('How the reference pictures align with the target video — Picture 1 (from Shot 1) '
                  'aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) '
                  'aligns with the 7.29-second mark of the target video.\n\n'
                  'integrated_multimodal_description: [Shot 1] a static medium side view of the '
                  'prototype starting up.\n\n'
                  'overall_soundscape: A low hum.\n\nnon_diegetic_music: N/A')
        problems = writing_layer_problems(prompt)
        self.assertTrue(any(p.startswith('R1') for p in problems))
        self.assertTrue(any(p.startswith('R3') for p in problems))
        self.assertTrue(any(p.startswith('baseline') for p in problems))

    def test_compliant_prompt_lints_clean(self):
        from workflow.film_ir import writing_layer_problems
        desc = ('[Shot 1] The camera holds a static shot for the entire duration, keeping the '
                'framing fixed while ' + 'the machine runs steadily and the vapor rises. ' * 20)
        prompt = ('How the reference pictures align with the target video — Picture 1 (from Shot 1) '
                  'aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) '
                  'aligns with the 7.29-second mark of the target video.\n\n'
                  'integrated_multimodal_description: ' + desc + '\n\n'
                  'overall_soundscape: A low hum opens the scene. The hum deepens as the machine '
                  'starts. Steam hiss joins midway through.\n\nnon_diegetic_music: N/A')
        self.assertEqual(writing_layer_problems(prompt), [])


class OfficialExamplesPassCleanTest(unittest.TestCase):
    """Controls-gate seed (open-h3-ir discipline): the vendored official skill's
    own examples MUST validate clean — a rule that fires on the spec's artifact
    is a wrong rule. Boundary fact: T2VA carries no alignment line by design."""

    def _cases(self):
        import re
        from pathlib import Path
        text = Path('docs/skills/h3-prompt-writing/references/base-en.txt').read_text()
        return {m.group(2): m.group(4) for m in re.finditer(
            r'### Case (\d): (T2VA|I2VA|FL2VA|L2VA)(.*?)```text\n(.*?)```', text, re.S)}

    def test_official_anchored_examples_pass(self):
        cases = self._cases()
        self.assertIsNone(film_ir.fl2va_problem(cases['I2VA'], 'first_only', 8, False))
        self.assertIsNone(film_ir.fl2va_problem(cases['FL2VA'], 'first_last', 8.0, True))

    def test_live_official_api_outputs_pass(self):
        # Live golden corpus: official Context-IR API outputs for our real shots
        # (probe 2026-10-06). Filename encodes <shot>_<tier_seconds>.txt; the
        # expected Picture-2 mark is the NATIVE frame-grid seconds of that tier
        # (official ruling: 7s request -> 7.29 mark, 6s -> 6.58, 8s -> 8.00).
        from pathlib import Path
        import re as _re
        from workflow.film_ir_plan import _native_frames
        golden = sorted(Path('research/official_ir_golden').glob('*_*.txt'))
        if not golden:
            self.skipTest('no golden artifacts present')
        for f in golden:
            with self.subTest(golden=f.name):
                m = _re.match(r'(\w+)_(\d+)\.txt', f.name)
                self.assertIsNotNone(m, f'unparseable golden name {f.name}')
                native = _native_frames(int(m.group(2))) / 24
                self.assertIsNone(
                    film_ir.fl2va_problem(f.read_text(), 'first_last', native, True))

    def test_official_unanchored_examples_carry_no_alignment_line(self):
        cases = self._cases()
        for kind in ('T2VA', 'L2VA'):
            first = cases[kind].splitlines()[0]
            self.assertNotIn('Picture 1', first.split('integrated')[0][:60] or first[:60])


class IdentityRestatementTest(unittest.TestCase):
    """Wardrobe-drift rule: naming a subject without restating its persistent
    identity tokens warns (never blocks) — state-once subjects drift."""

    def _plan_shot(self, prompt, subject_identity=None):
        shot = {'id': 'S06', 'prompt': prompt, 'duration_seconds': 7.4625,
                'reference_images': ['a.png', 'b.png']}
        plan = {'id': 'p1', 'subject_identity': subject_identity,
                'visual_continuity': '', 'film_ir_prompt_contract': 'h3_fl2va_v1',
                'shots': [shot]}
        return plan, shot

    def _kinds(self, plan, shot):
        from workflow.film_ir_plan import compile_shot_from_plan
        m = compile_shot_from_plan(plan, shot, {'remote_root': '/tmp/x'})
        return [v['kind'] for v in m['violations']]

    def test_named_subject_without_restate_warns(self):
        plan, shot = self._plan_shot(
            'How the reference pictures align with the target video — Picture 1 (from Shot 1) '
            'aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) '
            'aligns with the 8.00-second mark of the target video.\n\n'
            'integrated_multimodal_description: [Shot 1] the prototype starts up.\n\n'
            'overall_soundscape: hum.\n\nnon_diegetic_music: N/A',
            [{'id': 'unit', 'name_tokens': ['prototype', 'cylindrical heat-pump'],
              'must_restate': ['silver-grey', 'right-side steam outlet']}])
        kinds = self._kinds(plan, shot)
        self.assertIn('identity_restatement_warning', kinds)
        self.assertNotIn('h3_fl2va_invalid', kinds)  # warning, never blocking

    def test_restatement_present_is_clean(self):
        plan, shot = self._plan_shot(
            'How the reference pictures align with the target video — Picture 1 (from Shot 1) '
            'aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) '
            'aligns with the 8.00-second mark of the target video.\n\n'
            'integrated_multimodal_description: [Shot 1] the silver-grey cylindrical heat-pump '
            'prototype starts up.\n\noverall_soundscape: hum.\n\nnon_diegetic_music: N/A',
            [{'id': 'unit', 'name_tokens': ['prototype'],
              'must_restate': ['silver-grey']}])
        self.assertNotIn('identity_restatement_warning', self._kinds(plan, shot))

    def test_no_subject_spec_or_no_name_hit_is_silent(self):
        plan, shot = self._plan_shot('integrated_multimodal_description: a park.\n\n'
                                     'overall_soundscape: wind.\n\nnon_diegetic_music: N/A',
                                     None)
        self.assertNotIn('identity_restatement_warning', self._kinds(plan, shot))
        plan, shot = self._plan_shot(
            'integrated_multimodal_description: no subjects named here.\n\n'
            'overall_soundscape: wind.\n\nnon_diegetic_music: N/A',
            [{'id': 'unit', 'name_tokens': ['prototype'], 'must_restate': ['silver-grey']}])
        self.assertNotIn('identity_restatement_warning', self._kinds(plan, shot))


class H3OfficialFL2VAContractTest(unittest.TestCase):
    """h3_fl2va_v1: the official alignment line IS the binding; the compiler
    validates it against the compiled slot instead of appending a suffix."""

    def _shot(self, prompt, contract='h3_fl2va_v1', conditioning='first_last'):
        return {'id': 'S1', 'slot_start': 0.0, 'slot_seconds': 6.6, 'seed': 7,
                'conditioning': conditioning, 'prompt_authored': prompt,
                'prompt_contract': contract,
                'keyframes': {'start': ['k0.png'], 'end': ['k1.png']}}

    def _compile(self, shot):
        from workflow import film_ir
        violations = []
        film_ir.compile_shot(shot, {'name': 't', 'seconds': 7, 'frames': 175,
                                    'steps': 50, 'short_edge': 768}, violations)
        return violations

    def _official(self, end='7.29'):  # 7s tier -> 175 frames -> 7.29s native mark
        return (f'How the reference pictures align with the target video — Picture 1 (from Shot 1) '
                f'aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) '
                f'aligns with the {end}-second mark of the target video.\n'
                f'\nintegrated_multimodal_description: [Shot 1] Live-action documentary style.\n'
                f'\noverall_soundscape: A low hum.\n'
                f'\nnon_diegetic_music: N/A')

    def test_official_prompt_passes_without_suffix(self):
        shot = self._shot(self._official())
        v = self._compile(shot)
        self.assertFalse([x for x in v if x['kind'] == 'h3_fl2va_invalid'], v)
        segs = self._compile_prompt(shot)
        self.assertNotIn('Begin exactly', segs[0]['request']['prompt'])

    def _compile_prompt(self, shot):
        from workflow import film_ir
        violations = []
        m = film_ir.compile_shot(shot, {'name': 't', 'seconds': 7, 'frames': 175,
                                        'steps': 50, 'short_edge': 768}, violations)
        return m['segments'] if m else []

    def test_wrong_slot_mark_blocked(self):
        shot = self._shot(self._official(end='8.00'))  # tier 7 native mark is 7.29; 8.00 is wrong
        v = self._compile(shot)
        self.assertTrue(any(x['kind'] == 'h3_fl2va_invalid' for x in v), v)

    def test_integer_tier_mark_also_blocked(self):
        # Official timebase ruling: for a 7s request the mark must be the native
        # 7.29, NOT the integer 7.00 (ceil/requested-timebase marks are invalid).
        shot = self._shot(self._official(end='7.00'))
        v = self._compile(shot)
        self.assertTrue(any(x['kind'] == 'h3_fl2va_invalid' for x in v), v)

    def test_missing_field_blocked(self):
        prompt = self._official().replace('overall_soundscape: A low hum.', '')
        v = self._compile(self._shot(prompt))
        self.assertTrue(any(x['kind'] == 'h3_fl2va_invalid' for x in v))

    def test_blocking_classification(self):
        from workflow.film_ir_plan import BLOCKING_VIOLATIONS
        self.assertIn('h3_fl2va_invalid', BLOCKING_VIOLATIONS)

    def test_legacy_contracts_untouched(self):
        shot = self._shot('一句中文提示词。', contract='legacy_verbatim')
        segs = self._compile_prompt(shot)
        self.assertEqual(segs[0]['request']['prompt'], '一句中文提示词。')

    def test_empty_field_content_blocked(self):
        prompt = (self._official().rsplit('overall_soundscape:', 1)[0]
                  + 'overall_soundscape:\n\nnon_diegetic_music: N/A')
        v = self._compile(self._shot(prompt))
        self.assertTrue(any(x['kind'] == 'h3_fl2va_invalid' for x in v))

    def test_fields_crammed_one_line_blocked(self):
        prompt = (self._official().split('\n\n', 1)[0]
                  + '\nintegrated_multimodal_description: x overall_soundscape: y non_diegetic_music: z')
        v = self._compile(self._shot(prompt))
        self.assertTrue(any(x['kind'] == 'h3_fl2va_invalid' for x in v))

    def test_missing_blank_line_blocked(self):
        head, body = self._official().split('\n\n', 1)
        v = self._compile(self._shot(head + '\n' + body))
        self.assertTrue(any(x['kind'] == 'h3_fl2va_invalid' for x in v))

    def test_duplicate_field_blocked(self):
        prompt = self._official() + '\n\nnon_diegetic_music: N/A'
        v = self._compile(self._shot(prompt))
        self.assertTrue(any(x['kind'] == 'h3_fl2va_invalid' for x in v))
