"""Coordinator fault tests with real SQLite/process death; no media/API claims.

The fake provider has a durable submission ledger. Reconciliation is allowed to
run after process death, but cannot append a second submission for that input.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from workflow.plan import sha256, write_json

ROOT = Path(__file__).resolve().parents[1]


def packet(root, names=('a', 'b')):
    rows = []
    for name in names:
        folder = root / name
        row = {'product_id': name, 'max_images': 2}
        for field in ('manifest', 'draft', 'narrative_review'):
            path = folder / (field + '.json')
            write_json(path, {'product_id': name, 'kind': field, 'version': 1})
            row[field + '_path'] = str(path)
            row[field + '_sha256'] = sha256(path)
        rows.append(row)
    path = root / 'packet.json'
    write_json(path, {'schema_version': 1, 'products': rows})
    return path


def success(phase):
    # Updated against the coordinator's explicit phase contract, not verdicts.
    return {'state': 'delivered' if phase == 'delivery' else 'pending',
            'phase': {'assets': 'downstream', 'downstream': 'delivery', 'delivery': 'delivery'}[phase]}


class CoordinatorFaultTests(unittest.TestCase):
    def setUp(self):
        from workflow.pipeline_coordinator import Coordinator
        self.Coordinator = Coordinator
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'coordinator.sqlite'

    def test_failed_product_does_not_block_second_and_is_not_retried(self):
        calls = []
        def adapter(spec, phase, workdir):
            calls.append((spec['product_id'], phase))
            if spec['product_id'] == 'a':
                raise ValueError('deterministic product-local malformed evidence')
            return success(phase)
        c = self.Coordinator(self.db, adapter=adapter)
        c.register(packet(self.root))
        for _ in range(4): c.tick(workers=2)
        rows = {x['product_id']: x for x in c.status()}
        self.assertTrue(rows['a']['state'].startswith('isolated'), rows)
        self.assertEqual(rows['b']['state'], 'delivered')
        self.assertEqual(sum(p == 'a' for p, _ in calls), 1)
        self.assertEqual([phase for p, phase in calls if p == 'b'], ['assets', 'downstream', 'delivery'])

    def test_completed_input_tampering_invalidates_without_reentering_adapter(self):
        calls = []
        def adapter(spec, phase, workdir):
            calls.append(phase)
            return success(phase)
        p = packet(self.root, ('a',))
        c = self.Coordinator(self.db, adapter=adapter); c.register(p)
        for _ in range(4): c.tick()
        self.assertEqual(c.status()[0]['state'], 'delivered')
        Path(json.loads(p.read_text())['products'][0]['manifest_path']).write_text('{"tampered":true}')
        restarted = self.Coordinator(self.db, adapter=adapter); restarted.tick()
        self.assertEqual(restarted.status()[0]['state'], 'invalidated')
        self.assertEqual(calls, ['assets', 'downstream', 'delivery'])

    def test_packet_tampering_invalidates_even_when_nested_files_unchanged(self):
        calls = []
        p = packet(self.root, ('a',))
        c = self.Coordinator(self.db, adapter=lambda *a: calls.append(a) or success(a[1]))
        c.register(p)
        original = json.loads(p.read_text()); original['operator_note'] = 'changed packet'
        write_json(p, original); c.tick()
        self.assertEqual(c.status()[0]['state'], 'invalidated')
        self.assertEqual(calls, [])

    def test_sigkill_after_durable_provider_intent_reconciles_without_resubmit(self):
        p = packet(self.root, ('a',))
        c = self.Coordinator(self.db, adapter=lambda *a: success(a[1])); c.register(p)
        child = r'''
import json,sys,time
from pathlib import Path
from workflow.pipeline_coordinator import Coordinator
root=Path(sys.argv[1])
def adapter(spec,phase,workdir):
    with (root/'provider-submissions.jsonl').open('a') as f:
        f.write(json.dumps({'product_id':spec['product_id'],'phase':phase})+'\n');f.flush()
        import os;os.fsync(f.fileno())
    (root/'intent-committed').write_text('ready')
    while True: time.sleep(0.05)
Coordinator(root/'coordinator.sqlite',adapter=adapter).tick(workers=1)
'''
        process = subprocess.Popen([sys.executable, '-c', child, str(self.root)], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 10
        while not (self.root / 'intent-committed').exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue((self.root / 'intent-committed').exists(), process.communicate(timeout=1) if process.poll() is not None else 'child did not commit intent')
        # A second dispatcher must not advance the same SQLite ledger in parallel.
        with self.assertRaises(BlockingIOError):
            self.Coordinator(self.db, adapter=lambda *a: self.fail('foreign dispatcher ran')).tick()
        process.kill(); process.communicate(timeout=5)
        phases = []
        def reconcile(spec, phase, workdir):
            phases.append(phase)
            if phase == 'assets':
                # Provider outcome is unknown: no new invocation permitted.
                self.assertTrue((self.root / 'intent-committed').exists())
                return {'state': 'awaiting_provider_reconciliation', 'phase': phase,
                        'detail': {'submission_unknown': True, 'automatic_resubmit': False}}
            self.fail('Unknown assets must not advance to downstream')
        restarted = self.Coordinator(self.db, adapter=reconcile)
        restarted.tick(workers=1); restarted.tick(workers=1)
        self.assertEqual(phases, ['assets', 'assets'])
        self.assertEqual(len((self.root / 'provider-submissions.jsonl').read_text().splitlines()), 1)
        self.assertEqual(restarted.status()[0]['state'], 'awaiting_provider_reconciliation')

    def test_invalid_product_in_same_packet_does_not_block_valid_peer(self):
        p = packet(self.root)
        data = json.loads(p.read_text())
        data['products'][0].pop('draft_sha256')
        write_json(p, data)
        c = self.Coordinator(self.db, adapter=lambda spec, phase, work: success(phase))
        c.register(p)
        for _ in range(3):
            c.tick()
        rows = {r['product_id']:r for r in c.status()}
        self.assertEqual(rows['a']['state'], 'isolated_intake')
        self.assertEqual(rows['b']['state'], 'delivered')

    def test_repeat_registration_preserves_waiting_identity(self):
        p = packet(self.root, ('a',))
        c = self.Coordinator(self.db, adapter=lambda spec, phase, workdir:
                             {'state': 'awaiting_provider_reconciliation', 'phase': phase})
        first = c.register(p); c.tick(); second = c.register(p)
        self.assertEqual(first, second)
        self.assertEqual(len(c.status()), 1)
        self.assertEqual(c.status()[0]['state'], 'awaiting_provider_reconciliation')

    def test_generic_path_sha_audit_context_tampering_invalidates(self):
        p = packet(self.root, ('a',))
        evidence = self.root / 'content-hold.json'
        write_json(evidence, {'blocks_registration': True})
        plan = json.loads(p.read_text())
        plan['products'][0]['audit_context'] = [{'path': str(evidence), 'sha256': sha256(evidence)}]
        write_json(p, plan)
        calls = []
        c = self.Coordinator(self.db, adapter=lambda *a: calls.append(a) or success(a[1]))
        c.register(p)
        write_json(evidence, {'blocks_registration': False})
        c.tick()
        self.assertEqual(c.status()[0]['state'], 'invalidated')
        self.assertEqual(calls, [])

    def test_completed_review_evidence_tampering_invalidates(self):
        calls = []
        p = packet(self.root, ('a',))
        c = self.Coordinator(self.db, adapter=lambda *a: calls.append(a[1]) or success(a[1]))
        c.register(p)
        for _ in range(3): c.tick()
        review = Path(json.loads(p.read_text())['products'][0]['narrative_review_path'])
        review.write_text('{}')
        self.Coordinator(self.db, adapter=lambda *a: self.fail('must not execute')).tick()
        self.assertEqual(c.status()[0]['state'], 'invalidated')

    def test_diagnosed_pre_submission_recovery_keeps_ledger_and_rejects_claims(self):
        p=packet(self.root,('a',));c=self.Coordinator(self.db)
        identity=c.register(p)[0];error={'error_type':'KeyError','reason':"'aspect_ratio'"}
        c.record(identity,'assets','isolated_error',error)
        diagnosis=self.root/'diagnosis.json';write_json(diagnosis,{'fixed':'canvas precheck'})
        work=self.db.parent/'versions'/identity
        write_json(work/'images/task.json',{'state':'pending'})
        self.assertEqual(c.recover_unsubmitted_assets(identity,error,diagnosis,sha256(diagnosis)),identity)
        self.assertEqual(c.status()[0]['state'],'pending')
        c.record(identity,'assets','isolated_error',error)
        write_json(work/'images/task.json',{'state':'pending','claim_token':'already-claimed'})
        with self.assertRaises(ValueError):c.recover_unsubmitted_assets(identity,error,diagnosis,sha256(diagnosis))
        self.assertEqual(c.status()[0]['state'],'isolated_error')

    def test_local_binding_recovery_cannot_retry_an_unknown_review(self):
        p=packet(self.root,('a',));c=self.Coordinator(self.db);identity=c.register(p)[0]
        c.record(identity,'downstream','isolated_error',{'error_type':'ValueError','reason':'Incomplete bound board: S02:previsual_path'})
        diagnosis=self.root/'diagnosis.json';write_json(diagnosis,{'fix':'extract source frame'})
        work=self.db.parent/'versions'/identity
        cp=work/'downstream/review.checkpoint.json';write_json(cp,{'state':'running'})
        with self.assertRaises(ValueError):c.recover_local_downstream(identity,diagnosis,sha256(diagnosis))
        cp.unlink();write_json(work/'downstream/bind.checkpoint.json',{'state':'failed'})
        c.recover_local_downstream(identity,diagnosis,sha256(diagnosis))
        self.assertEqual(c.status()[0]['state'],'pending')
        self.assertTrue(c.status()[0]['detail']['same_ledger'])

    def test_real_pending_image_payload_is_validated_before_claim(self):
        from workflow.storyboard_image_jobs import ImageJobs, _canvas
        from workflow.external_image_executor import payload_for
        plan = {'aspect_ratio':'9:16', 'shots':[{'id':'S01','prompt':'A box',
                'keyframe_prompts':{'start':'A box','end':'A moved box'},
                'storyboard':{'state_before':{},'state_after':{}}}]}
        path=self.root/'plan.json';write_json(path,plan)
        jobs=ImageJobs(self.root/'images');jobs.add(path,'a')
        task=json.loads(next(jobs.folder.glob('*.json')).read_text())
        self.assertEqual(task['state'],'pending')
        self.assertNotIn('aspect_ratio',task)
        payload=payload_for({**task,**_canvas(plan)})
        self.assertEqual(payload['aspect_ratio'],'9:16')
        self.assertEqual(json.loads(next(jobs.folder.glob('*.json')).read_text())['state'],'pending')

    def test_runtime_mismatch_waits_before_charging_then_same_version_resumes(self):
        from unittest.mock import patch
        from workflow import media, storyboard, review
        from workflow.pipeline_coordinator import production_step
        from workflow.storyboard_downstream import Paused
        p = packet(self.root, ('a',))
        data = json.loads(p.read_text())
        runtime = self.root / 'runtime.json'
        data['renderer_runtime_path'] = str(runtime)
        write_json(p, data)
        def receipt(media_sha):
            return {'pid': os.getpid(), 'loaded_media_sha256': media_sha,
                    'loaded_review_sha256': review.REVIEW_CODE_SHA256,
                    'normalization_version': 'whole_action_v1', 'story_preflight_lane': True,
                    'story_review_version': 'shot-review-v3-explicit-endpoint-evidence',
                    'story_preflight_version': storyboard.PREFLIGHT_VERSION}
        write_json(runtime, receipt('old-renderer-code'))
        c = self.Coordinator(self.db)
        ids = c.register(p)
        c.record(ids[0], 'downstream', 'pending', {})
        with patch('workflow.narrative_gate.require_review'), patch('workflow.storyboard_downstream.run_product') as downstream:
            c.tick()
            self.assertEqual(c.status()[0]['state'], 'awaiting_runtime')
            downstream.assert_not_called()
            write_json(runtime, receipt(media.MEDIA_CODE_SHA256))
            downstream.return_value = {'state': 'awaiting_images'}
            self.Coordinator(self.db).tick()
            self.assertEqual(c.status()[0]['state'], 'awaiting_images')
            downstream.assert_called_once()
            self.assertEqual(c.status()[0]['id'], ids[0])
        # Assets are protected by the same gate before ImageJobs/ExternalImages.
        spec = c.status()[0]['spec']
        write_json(runtime, receipt('old-renderer-code'))
        with patch('workflow.narrative_gate.require_review'), patch('workflow.external_image_executor.ExternalImages') as images:
            result = production_step(spec, 'assets', self.root / 'work')
            self.assertEqual(result['state'], 'awaiting_runtime')
            images.assert_not_called()

    def test_production_delivery_consumes_current_index_and_driver_schemas(self):
        from unittest.mock import patch
        from workflow.pipeline_coordinator import production_step
        work = self.root / 'work'; work.mkdir()
        manifest = self.root / 'manifest.json'; write_json(manifest, {})
        request = self.root / 'request.json'
        write_json(request, {'state': 'completed', 'job_id': 'a-v2'})
        write_json(work / 'registration.json', {'registration': {'path': str(request)}})
        folder = self.root / 'user_repair30_20261002'
        driver = {'product_id': 'a', 'new_job_id': 'a-v2', 'job_id': 'a-old',
                  'request_id': 'r', 'state': 'completed', 'manifest_path': str(manifest), 'audit_path': 'audit'}
        write_json(folder / 'driver_status.json', {'items': {'r': driver}})
        video = self.root / 'video.mp4'; video.write_bytes(b'fake-video-no-media-approval-claimed')
        exported = {'product_id': 'a', 'version_job_id': 'a-v2', 'request_id': 'r',
                    'video_sha256': sha256(video), 'files': {'video.mp4': {'path': str(video), 'sha256': sha256(video)}}}
        write_json(folder / 'delivery_index.json', {'items': [exported]})
        write_json(video.parent / 'delivery_receipt.json', exported)
        spec = {'product_id': 'a', 'manifest_path': str(manifest), 'narrative_review_path': 'review',
                'production_root': str(self.root)}
        with patch('workflow.narrative_gate.require_review'), patch('workflow.repair_delivery.validate_product', return_value={'video_sha256': sha256(video), 'version_job_id': 'a-v2', 'sources': {'video.mp4': str(video)}, 'evidence': {}}) as validate:
            result = production_step(spec, 'delivery', work)
        self.assertEqual(result['state'], 'delivered')
        self.assertEqual(validate.call_args.args[1], driver)
        # A self-consistent export receipt cannot substitute for the verified render.
        with patch('workflow.narrative_gate.require_review'), patch('workflow.repair_delivery.validate_product', return_value={'video_sha256': 'different-verified-video'}):
            with self.assertRaises(ValueError):
                production_step(spec, 'delivery', work)

    def test_isolated_manifest_cannot_buy_new_attempt_via_filename_and_budget(self):
        calls = []
        def adapter(spec, phase, workdir):
            calls.append(spec['product_id'])
            return {'state': 'isolated_images', 'phase': phase}
        original = packet(self.root, ('a',))
        c = self.Coordinator(self.db, adapter=adapter)
        identity = c.register(original); c.tick()
        replacement = self.root / 'bigger-budget-renamed.json'
        content = json.loads(original.read_text())
        content['products'][0]['max_images'] = 64
        write_json(replacement, content)
        restarted = self.Coordinator(self.db, adapter=adapter)
        restarted.register(replacement)
        restarted.tick()
        self.assertEqual(calls, ['a'])
        rows = {row['id']: row for row in restarted.status()}
        self.assertEqual(rows[identity[0]]['state'], 'isolated_images')
        refused = [row for key, row in rows.items() if key not in identity]
        self.assertEqual(refused[0]['state'], 'isolated_intake')
        self.assertIn('original ledger', refused[0]['detail']['reason'])

    def test_version_conflict_in_same_packet_does_not_rollback_valid_peer(self):
        original = packet(self.root / 'original', ('a',))
        c = self.Coordinator(self.db, adapter=lambda spec, phase, work: success(phase))
        c.register(original)
        mixed = packet(self.root / 'mixed', ('a', 'b'))
        rows = json.loads(mixed.read_text())
        rows['products'][0] = json.loads(original.read_text())['products'][0]
        write_json(mixed, rows)
        c.register(mixed)
        for _ in range(3): c.tick()
        results = c.status()
        self.assertEqual(next(r for r in results if r['product_id'] == 'b')['state'], 'delivered')
        conflicts = [r for r in results if r['state'] == 'isolated_intake']
        self.assertEqual(len(conflicts), 1)
        self.assertIn('original ledger', conflicts[0]['detail']['reason'])
        restarted = self.Coordinator(self.db, adapter=c.adapter)
        restarted.register(mixed)
        self.assertEqual(len(restarted.status()), 3)

    def test_real_inbox_bad_packet_does_not_starve_good_and_restart_keeps_outcomes(self):
        inbox = self.root / 'inbox'; inbox.mkdir()
        (inbox / '00-bad.json').write_text('{invalid JSON')
        valid = packet(self.root / 'payload', ('good',))
        (inbox / '01-good.json').write_bytes(valid.read_bytes())
        calls = []
        c = self.Coordinator(self.db, adapter=lambda spec, phase, workdir:
                             calls.append((spec['product_id'], phase)) or success(phase))
        results = c.discover(inbox)
        self.assertEqual([x['state'] for x in results], ['isolated_intake', 'registered'])
        with c.connect() as db:
            persisted = db.execute('SELECT path,outcome FROM pipeline_inbox ORDER BY path').fetchall()
        self.assertEqual(len(persisted), 2)
        restarted = self.Coordinator(self.db, adapter=c.adapter)
        self.assertEqual(restarted.discover(inbox), [])
        for _ in range(3): restarted.tick()
        self.assertEqual(restarted.status()[0]['state'], 'delivered')
        self.assertEqual(calls, [('good', 'assets'), ('good', 'downstream'), ('good', 'delivery')])


if __name__ == '__main__':
    unittest.main()
