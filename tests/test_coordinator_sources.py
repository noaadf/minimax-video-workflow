import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow.plan import sha256, write_json
from workflow.coordinator_sources import build_sources
from workflow.storyboard_downstream import Paused


class DerivedSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.work = self.root / 'work'; self.work.mkdir()
        image = self.root / 'image.png'; image.write_bytes(b'fixture-image')
        self.builder = self.root / 'builder.py'
        self.builder.write_text('''import argparse, pathlib, subprocess
p=argparse.ArgumentParser();p.add_argument('--inputs-json');p.add_argument('--output');a=p.parse_args()
c=pathlib.Path(a.output+'.calls');c.write_text(c.read_text()+'x' if c.exists() else 'x')
subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=32x32:r=10','-t','0.2','-c:v','libx264','-pix_fmt','yuv420p',a.output],check=True)
''')
        self.output = self.root / 'derived.mp4'
        self.plan = {'shots':[{'id':'S01'}, {'id':'S02','source_path':str(self.output)}]}
        self.spec = {'source_builders':[{'shot_id':'S02','depends_on':[{'shot_id':'S01','role':'start'}],
                     'builder_path':str(self.builder),'builder_sha256':sha256(self.builder)}]}
        self.tasks = [{'shot_id':'S01','role':'start','state':'completed','image_path':str(image),'image_sha256':sha256(image)}]

    def test_real_local_clip_replays_receipt_not_builder_and_detects_tampering(self):
        receipts = build_sources(self.spec,self.plan,self.tasks,self.work)
        self.assertEqual(receipts[0]['sha256'],sha256(self.output))
        build_sources(self.spec,self.plan,self.tasks,self.work)
        self.assertEqual(Path(str(self.output)+'.calls').read_text(),'x')
        self.output.write_bytes(b'changed')
        with self.assertRaises(ValueError): build_sources(self.spec,self.plan,self.tasks,self.work)
        self.assertEqual(Path(str(self.output)+'.calls').read_text(),'x')

    def test_missing_dependency_does_not_start_builder(self):
        with self.assertRaises(Paused) as exc: build_sources(self.spec,self.plan,[],self.work)
        self.assertEqual(exc.exception.state,'awaiting_source_dependency')
        self.assertFalse(self.output.exists())

    def test_unreceipted_output_never_overwritten_or_blindly_retried(self):
        self.output.write_bytes(b'old')
        with self.assertRaises(ValueError): build_sources(self.spec,self.plan,self.tasks,self.work)
        with self.assertRaises(Paused): build_sources(self.spec,self.plan,self.tasks,self.work)
        self.assertEqual(self.output.read_bytes(),b'old')

    def test_asset_stage_completes_images_before_waiting_for_dependent_source(self):
        from workflow.pipeline_coordinator import production_step
        manifest = self.root / 'manifest.json'
        write_json(manifest,{'shots':[{'id':'S01','keyframe_prompts':{'start':'A box'}},
                        {'id':'S02','production_mode':'deterministic_graphic','source_path':str(self.output)}]})
        spec={'manifest_path':str(manifest),'manifest_sha256':sha256(manifest),'narrative_review_path':'test',
              'product_id':'a','max_images':1}
        folder=self.work/'images';folder.mkdir()
        class Jobs:
            def __init__(self,*a): self.folder=folder
            def add(self,*a): write_json(folder/'task.json',{'state':'pending','id':'one'})
        class Images:
            def __init__(self,*a): pass
            def run(self,**kw): write_json(folder/'task.json',{'state':'completed','id':'one'})
        with patch('workflow.narrative_gate.require_review'), patch('workflow.pipeline_coordinator.infrastructure_ready'), patch('workflow.plan.validate'), patch('workflow.storyboard_downstream.validate_source_timing'), patch('workflow.first_frame.reference_roles',return_value=['start']), patch('workflow.storyboard_image_jobs.ImageJobs',Jobs), patch('workflow.external_image_executor.ExternalImages',Images), patch('workflow.external_image_executor.payload_for'):
            result=production_step(spec,'assets',self.work)
        self.assertEqual(result['state'],'awaiting_source_material')
        self.assertEqual(result['detail']['image_tasks_completed'],1)
        self.assertEqual(result['detail']['shots'],['S02'])

    def test_source_preview_is_real_idempotent_and_tamper_checked(self):
        from workflow.coordinator_sources import source_preview
        receipt = build_sources(self.spec,self.plan,self.tasks,self.work)[0]
        frame = source_preview(receipt,self.plan['shots'][1],self.work/'previews')
        from PIL import Image
        with Image.open(frame['path']) as im:
            self.assertEqual(im.size,(32,32))
        self.assertEqual(source_preview(receipt,self.plan['shots'][1],self.work/'previews'),frame)
        self.assertEqual(Path(str(self.output)+'.calls').read_text(),'x')
        Path(frame['path']).write_bytes(b'tampered')
        with self.assertRaises(ValueError):source_preview(receipt,self.plan['shots'][1],self.work/'previews')

    def test_candidate_binds_existing_image_and_two_sources_without_mutating_parent(self):
        from workflow.coordinator_sources import prepare_source_candidate
        import copy
        second=self.root/'second.mp4'
        self.plan['shots'].append({'id':'S03','source_path':str(second)})
        self.spec['source_builders'].append({**self.spec['source_builders'][0],'shot_id':'S03'})
        manifest=self.root/'manifest.json';write_json(manifest,self.plan)
        original=manifest.read_bytes()
        spec={**self.spec,'manifest_path':str(manifest),'manifest_sha256':sha256(manifest),'narrative_review_path':'review'}
        asset={'shot_id':'S01','role':'start','path':self.tasks[0]['image_path'],'sha256':self.tasks[0]['image_sha256']}
        with patch('workflow.narrative_gate.require_review'),patch('workflow.storyboard_downstream.complete_assets',return_value=[asset]):
            item=prepare_source_candidate(spec,self.plan,self.tasks,self.work)
            again=prepare_source_candidate(spec,self.plan,self.tasks,self.work)
        self.assertEqual(item,again)
        self.assertEqual(manifest.read_bytes(),original)
        self.assertNotEqual(item['manifest_sha256'],spec['manifest_sha256'])
        bound=json.loads(Path(item['manifest_path']).read_text())
        self.assertEqual(bound['shots'][0]['reference_images'],[asset['path']])
        for shot in bound['shots'][1:]:
            self.assertEqual(shot['previsual_sha256'],sha256(shot['previsual_path']))
            self.assertEqual(shot['source_sha256'],sha256(shot['source_path']))
        self.assertEqual(Path(str(self.output)+'.calls').read_text(),'x')
        self.assertEqual(Path(str(second)+'.calls').read_text(),'x')
        self.assertEqual(self.plan,json.loads(original))
