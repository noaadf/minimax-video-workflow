"""Supervisor adjudication: uncertainty holds clear with bound evidence; defects never do."""
import json
import tempfile
import unittest
from pathlib import Path

from workflow.supervisor_adjudication import VERSION, validate, apply_to
from workflow.delivery_policy import resolve


def frame(path, payload=b'x'):
    Path(path).write_bytes(payload)
    return str(path)


def base_quality(tmp, held_shots, verdict='review', editorial_findings=None):
    quality = {
        'passed': True,
        'storyboard_required': True,
        'editorial': {
            'status': 'review_required',
            'review': {'verdict': verdict, 'confidence': 0.85,
                       'findings': editorial_findings or [{'severity': 'low', 'category': 'visual_quality', 'issue': 'x'}]},
            'receipt': {'model': 'm'},
        },
        'editorial_status': 'review_required',
        'speech': {'status': 'transcript_match'},
        'shot_review': {'status': 'review_required' if held_shots else 'sampled_checks_passed',
                        'shots': [
                            {'shot_id': 'S1', 'status': 'sampled_checks_passed', 'review': {'verdict': 'pass', 'findings': [], 'checks': []}},
                            {'shot_id': 'S2', 'status': 'held', 'review': {'verdict': 'review', 'findings': [
                                {'severity': 'low', 'category': 'unknown', 'issue': 'static frames cannot confirm motion'}], 'checks': []}},
                        ]},
    }
    path = Path(tmp) / 'quality.json'
    path.write_text(json.dumps(quality))
    return quality, path


def record_for(tmp, quality_path, video, shots_spec, editorial=None):
    from workflow.plan import sha256
    shot_items = []
    for shot_id, disposition, obs in shots_spec:
        files, binding = [], {}
        for i in range(2):
            f = frame(Path(tmp) / f'{shot_id}_f{i}.jpg', f'{shot_id}{i}'.encode())
            files.append(f)
            binding[Path(f).name] = sha256(f)
        shot_items.append({'shot_id': shot_id, 'disposition': disposition, 'observation': obs,
                           'evidence_frames': files, 'evidence_sha256': binding})
    record = {'version': VERSION, 'reviewer': 'claude-supervisor', 'content_approved': False,
              'quality_sha256': sha256(quality_path),
              'video_sha256': sha256(video), 'shots': shot_items}
    if editorial:
        record['editorial'] = editorial
    path = Path(tmp) / 'supervisor_adjudication.json'
    path.write_text(json.dumps(record))
    return path, record


class SupervisorAdjudicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.video = Path(self.tmp) / 'video.mp4'
        self.video.write_bytes(b'video-bytes')

    def test_motion_hold_clears_and_gate_turns_ready(self):
        quality, qpath = base_quality(self.tmp, held_shots=['S2'])
        rpath, _ = record_for(self.tmp, qpath, self.video,
                              [('S2', 'motion_verified_by_frame_sequence', 'ore advanced 31%->22%')],
                              editorial={'disposition': 'confidence_hold_cleared', 'observation': 'low-only findings; frames reviewed'})
        cleared, editorial = validate(rpath, qpath, self.video)
        view = apply_to(quality, cleared, editorial)
        self.assertEqual(view['shot_review']['status'], 'sampled_checks_passed')
        self.assertEqual(view['editorial']['status'], 'supervisor_adjudicated')
        policy = resolve(view, {'quality_profile': 'strict_v1'})
        self.assertTrue(policy['ready_for_delivery'], policy['blockers'])

    def test_reject_and_material_findings_never_clear(self):
        quality, qpath = base_quality(self.tmp, held_shots=['S2'],
                                      editorial_findings=[{'severity': 'high', 'category': 'geometry', 'issue': 'bridge broken'}])
        rpath, _ = record_for(self.tmp, qpath, self.video,
                              [('S2', 'motion_verified_by_frame_sequence', 'ok')],
                              editorial={'disposition': 'confidence_hold_cleared', 'observation': 'x'})
        with self.assertRaisesRegex(ValueError, 'material'):
            validate(rpath, qpath, self.video)

    def test_changed_media_invalidates(self):
        quality, qpath = base_quality(self.tmp, held_shots=['S2'])
        rpath, _ = record_for(self.tmp, qpath, self.video,
                              [('S2', 'motion_verified_by_frame_sequence', 'ok')])
        self.video.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'media changed'):
            validate(rpath, qpath, self.video)

    def test_partial_coverage_rejected(self):
        quality, qpath = base_quality(self.tmp, held_shots=['S2'])
        quality['shot_review']['shots'].append(
            {'shot_id': 'S3', 'status': 'held', 'review': {'verdict': 'review', 'findings': [], 'checks': []}})
        qpath.write_text(json.dumps(quality))
        rpath, _ = record_for(self.tmp, qpath, self.video, [('S2', 'motion_verified_by_frame_sequence', 'ok')])
        with self.assertRaisesRegex(ValueError, 'exactly'):
            validate(rpath, qpath, self.video)


if __name__ == '__main__':
    unittest.main()
