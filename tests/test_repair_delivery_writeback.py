import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow.plan import sha256, write_json
from workflow.queue import Queue
from workflow.repair_delivery import complete


class RepairDeliveryWriteback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'output/production/user_repair30_20261002'
        self.q = Queue(self.root / 'output/production/queue.sqlite')
        with self.q.connect() as c:
            c.executescript('CREATE TABLE user_repair_requests(id TEXT PRIMARY KEY,job_id TEXT,status TEXT,video_path TEXT,video_sha256 TEXT); CREATE TABLE production_items(item_id TEXT PRIMARY KEY,current_job TEXT,state TEXT,detail TEXT,updated REAL);')
        self.lock = open(self.q.path + '.controller-service.lock', 'a')
        self.addCleanup(self.lock.close)
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The gate's standalone narrative tests cover narrative schemas. All
        # delivery binding, task hashes, audit coverage and audio checks run here.
        for name in ('require_review', 'validate_contract'):
            p = patch('workflow.repair_delivery.' + name)
            p.start(); self.addCleanup(p.stop)

    def fixture(self, product='p'):
        d = self.root / product; d.mkdir()
        def file(name):
            p = d / name; p.write_bytes(name.encode()); return str(p)
        old, video, shot, voice, music, page = [file(n) for n in ('old.mp4','video.mp4','shot.mp4','voice.wav','music.wav','page.jpg')]
        jid, rid = product + '-new', product + '-request'
        plan = {'id': jid, 'root_job_id': product, 'parent_job_id': product,
                'user_repair_request_id': rid, 'title': product,
                'shots': [{'id':'S01','prompt':'shot','duration_seconds':4}]}
        manifest = d / 'manifest.json'; write_json(manifest, plan)
        write_json(manifest.with_suffix('.narrative_review.json'), {'tested_separately':True})
        mix = d / 'mix.json'
        write_json(mix, {'source_audio_in_mix':False,'curated_effects':[],
                         'policy':{'mode':'independent_voice_continuous_music'},
                         'voice':{'path':voice,'sha256':sha256(voice)}, 'music':{'path':music,'sha256':sha256(music)}})
        results = {'S01':{'video_path':shot}, 'compose':{'audio_mix_path':str(mix)},
                   'quality':{'passed':True,'storyboard_required':True,'shot_review':{'status':'sampled_checks_passed'},
                              'speech':{'status':'transcript_match','review_required':False},
                              'editorial_status':'automatic_review_passed',
                              'editorial':{'status':'automatic_review_passed','receipt':{'model':'review'},
                                           'review':{'verdict':'pass','findings':[]}}},
                   'voice':{'audio_path':voice}, 'music':{'audio_path':music},
                   'export':{'video_path':video,'cover_path':file('cover.png'),'publication_path':file('publication.txt'),'export_dir':str(d)}}
        audit = d / 'audit.json'
        write_json(audit, {'job_id':jid,'product_id':product,'finished_at':1,'status':'sampled_checks_passed',
                          'receipt':{'model':'review','usage':{}},'review':{'shots':[{'shot_id':'S01','verdict':'pass','confidence':.99,'findings':[]}]},
                          'review_pages':[{'path':page,'sha256':sha256(page)}],
                          'evidence':[{'shot_id':'S01','video_path':shot,'source_sha256':sha256(shot),'sample_seconds':[0,2,3.9]}]})
        with self.q.connect() as c:
            c.execute('INSERT INTO jobs VALUES(?,?,?)',(product,'old',json.dumps({'id':product})))
            c.execute('INSERT INTO jobs VALUES(?,?,?)',(jid,'new',json.dumps(plan)))
            c.execute('INSERT INTO user_repair_requests VALUES(?,?,?,?,?)',(rid,product,'processing',old,sha256(old)))
            c.execute('INSERT INTO production_items VALUES(?,?,?,?,?)',(product,jid,'watching','{}',0))
            for stage, result in results.items():
                c.execute('INSERT INTO tasks(id,job_id,stage,capability,payload,deps,status,result) VALUES(?,?,?,?,?,?,?,?)',
                          (jid+':'+stage,jid,stage,'local','{}','[]','succeeded',json.dumps(result)))
        return {'product_id':product,'request_id':rid,'job_id':product,'new_job_id':jid,'manifest_path':str(manifest),'state':'auditing'}, audit

    def finish(self, item, audit):
        return complete(self.q,self.root,item,audit,self.lock.fileno())

    def test_legacy_quality_api_failure_is_advisory_after_owner_relaxation(self):
        # Owner-relaxed 2026-10-06: editorial API failure no longer blocks writeback;
        # the original quality record is still never mutated.
        item,audit=self.fixture()
        with self.q.connect() as c:
            row=c.execute("SELECT result FROM tasks WHERE id=?",(item['new_job_id']+':quality',)).fetchone()
            quality=json.loads(row['result']);quality['editorial']={'status':'review_required','error_type':'ModelResultError'}
            raw=json.dumps(quality)
            c.execute("UPDATE tasks SET result=? WHERE id=?",(raw,item['new_job_id']+':quality'))
        self.finish(item,audit)
        with self.q.connect() as c:
            self.assertEqual(c.execute("SELECT result FROM tasks WHERE id=?",(item['new_job_id']+':quality',)).fetchone()['result'],raw)
            self.assertEqual(c.execute("SELECT status FROM user_repair_requests WHERE id=?",(item['request_id'],)).fetchone()['status'],'completed')

    def test_two_products_replay_and_original_identity(self):
        a, ap = self.fixture('a'); b, bp = self.fixture('b')
        self.finish(a,ap); self.finish(b,bp); self.finish(a,ap)
        index = json.loads((self.folder/'delivery_index.json').read_text())
        self.assertEqual(index['count'],2)
        with self.q.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM events WHERE event='user_repair_completed'").fetchone()[0],2)
            self.assertEqual(c.execute('SELECT count(*) FROM production_items').fetchone()[0],2)
            self.assertEqual(c.execute("SELECT status FROM user_repair_requests WHERE id='a-request'").fetchone()[0],'completed')
        self.assertTrue((self.root/'a/old.mp4').exists())
        self.assertFalse((self.root/'exports/user_repair30_20261002/history').exists())

    def test_failure_isolation_and_transaction_rollback(self):
        a, ap = self.fixture('a'); b, bp = self.fixture('b')
        record=json.loads(ap.read_text());record['status']='audit_failed';write_json(ap,record)
        with self.assertRaises(ValueError):self.finish(a,ap)
        self.finish(b,bp)
        with self.q.connect() as c:
            self.assertEqual(c.execute("SELECT status FROM user_repair_requests WHERE id='a-request'").fetchone()[0],'processing')
        self.assertEqual(json.loads((self.folder/'delivery_index.json').read_text())['count'],1)

    def test_missing_last_sample_blocks_completion(self):
        item,audit=self.fixture();record=json.loads(audit.read_text());record['evidence'][0]['sample_seconds']=[0,1,2];write_json(audit,record)
        with self.assertRaisesRegex(ValueError,'samples'):self.finish(item,audit)

    def test_audio_and_export_integrity_block(self):
        item,audit=self.fixture();mix=Path(item['manifest_path']).with_name('mix.json');record=json.loads(mix.read_text());record['source_audio_in_mix']=True;write_json(mix,record)
        with self.assertRaisesRegex(ValueError,'clean audio'):self.finish(item,audit)
        record['source_audio_in_mix']=False;write_json(mix,record)
        with self.q.connect() as c:
            row=c.execute('SELECT result FROM tasks WHERE id=?',(item['new_job_id']+':export',)).fetchone();result=json.loads(row[0]);result['sha256']={result['video_path']:'0'*64};c.execute('UPDATE tasks SET result=? WHERE id=?',(json.dumps(result),item['new_job_id']+':export'))
        with self.assertRaisesRegex(ValueError,'artifact changed'):self.finish(item,audit)

    def test_content_quality_failure_and_changed_product_card_block(self):
        item,audit=self.fixture()
        with self.q.connect() as c:
            row=c.execute('SELECT result FROM tasks WHERE id=?',('p-new:quality',)).fetchone();result=json.loads(row[0]);result['passed']=False;c.execute('UPDATE tasks SET result=? WHERE id=?',(json.dumps(result),'p-new:quality'))
        with self.assertRaisesRegex(ValueError,'quality not ready'):self.finish(item,audit)
        result['passed']=True
        with self.q.connect() as c:
            c.execute('UPDATE tasks SET result=? WHERE id=?',(json.dumps(result),'p-new:quality'))
            c.execute("UPDATE production_items SET current_job='other' WHERE item_id='p'")
        with self.assertRaisesRegex(ValueError,'current product version'):self.finish(item,audit)

    def test_committed_database_recovers_filesystem_failure_without_duplicate(self):
        item,audit=self.fixture()
        with patch('workflow.repair_delivery.store_product_version',side_effect=OSError('disk interruption')):
            with self.assertRaises(OSError):self.finish(item,audit)
        self.finish(item,audit)
        with self.q.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM events').fetchone()[0],1)
        self.assertEqual(json.loads((self.folder/'delivery_index.json').read_text())['count'],1)

    def test_missing_owner_and_foreign_ancestor_rejected(self):
        item,audit=self.fixture()
        with self.assertRaises(ValueError):complete(self.q,self.root,item,audit,None)
        with self.q.connect() as c:
            c.execute('UPDATE jobs SET manifest=? WHERE id=?',(json.dumps({'id':'p','root_job_id':'other'}),'p'))
        with self.assertRaisesRegex(ValueError,'another product'):self.finish(item,audit)

    def test_intermediate_version_ancestry(self):
        item,audit=self.fixture();manifest=Path(item['manifest_path']);plan=json.loads(manifest.read_text());plan['parent_job_id']='p-middle';write_json(manifest,plan)
        with self.q.connect() as c:
            c.execute('UPDATE jobs SET manifest=? WHERE id=?',(json.dumps(plan),'p-new'))
            c.execute('INSERT INTO jobs VALUES(?,?,?)',('p-middle','middle',json.dumps({'id':'p-middle','root_job_id':'p','parent_job_id':'p'})))
        self.finish(item,audit)
        self.assertEqual(json.loads((self.folder/'delivery_index.json').read_text())['count'],1)
