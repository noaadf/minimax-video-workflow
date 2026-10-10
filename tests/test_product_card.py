"""Local-only T3 tests. Fixtures remain under /tmp; no file removals or APIs."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from workflow.delivery_policy import store_product_version
from workflow.plan import sha256, write_json
from workflow.products import product_id, suffix_chain, products, read_cards
from scripts.migrate_exports_to_cards import build_plan, main
from tests import test_repair_delivery_writeback as legacy_writeback


class KeptDirectory:
    """Retain local test fixtures as evidence; no cleanup removes files."""
    def __init__(self, *args, **kwargs):
        self.name = tempfile.mkdtemp(prefix='t3-writeback-')

    def cleanup(self):
        pass


class ProductCardTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='t3-product-card-'))

    def fixture(self, product='film', job='film-original', content=b'original', title='同一标题'):
        source = self.root / 'sources' / job
        source.mkdir(parents=True, exist_ok=True)
        for name, data in [('video.mp4', content), ('cover.png', b'cover'),
                           ('publication.txt', b'publication'), ('audit.json', b'{}')]:
            (source / name).write_bytes(data)
        manifest = {'id': job, 'root_job_id': product, 'title': title}
        verified = {'product_id': product, 'version_job_id': job, 'title': title,
                    'video_sha256': sha256(source / 'video.mp4'), 'system_ready': True,
                    'delivery_policy': {'ready_for_delivery': True, 'blockers': []},
                    'sources': {n: str(source / n) for n in ['video.mp4', 'cover.png', 'publication.txt']},
                    'evidence': {'audit': str(source / 'audit.json'), 'audit_sha256': sha256(source / 'audit.json')}}
        return verified, manifest

    def card(self, product='film'):
        return json.loads((self.root / 'output/production/runs' / product / 'card.json').read_text())

    def test_append_history_replay_preserves_original_and_flat_delivery(self):
        old, manifest = self.fixture()
        legacy = self.root / 'output/production/runs/film/abc/attempts'
        legacy.mkdir(parents=True); (legacy / 'old.mp4').write_bytes(b'legacy')
        first = store_product_version(self.root, old, manifest)
        new, repaired = self.fixture(job='film-repair', content=b'repair')
        repaired['parent_job_id'] = manifest['id']
        second = store_product_version(self.root, new, repaired, repair_reason='修复片尾')
        again = store_product_version(self.root, new, repaired, repair_reason='修复片尾')
        card = self.card()
        self.assertEqual([v['version'] for v in card['versions']], [1, 2])
        self.assertEqual(card['versions'][1]['repair_reason'], '修复片尾')
        self.assertEqual(second['exported_at'], again['exported_at'])
        self.assertEqual((self.root / 'output/production/runs/film/current').readlink(), Path('versions/2'))
        self.assertEqual(Path(first['files']['video.mp4']['path']).read_bytes(), b'original')
        self.assertTrue((legacy / 'old.mp4').exists())
        self.assertTrue(Path(old['sources']['video.mp4']).exists())
        self.assertEqual(len(list((self.root / 'exports').glob('*.mp4'))), 1)
        self.assertEqual(len(list((self.root / 'archives').rglob('*.mp4'))), 1)
        self.assertEqual(json.loads((self.root / 'exports/delivery_index.json').read_text())['count'], 1)
        store_product_version(self.root, old, manifest)  # Replay cannot rewind.
        self.assertEqual(self.card()['current_version'], 2)

    def test_prefix_collision_isolated_products(self):
        a, pa = self.fixture('production_aaa', 'a')
        b, pb = self.fixture('production_bbb', 'b')
        store_product_version(self.root, a, pa); store_product_version(self.root, b, pb)
        index = json.loads((self.root / 'exports/delivery_index.json').read_text())
        self.assertEqual(len({v['filename'] for v in index['items']}), 2)

    def test_blocked_changed_assets_evidence_and_symlink_refused(self):
        receipt, plan = self.fixture()
        receipt['delivery_policy']['blockers'] = ['hold']
        with self.assertRaises(ValueError): store_product_version(self.root, receipt, plan)
        self.assertFalse((self.root / 'exports').exists())
        receipt['delivery_policy']['blockers'] = []
        store_product_version(self.root, receipt, plan)
        Path(receipt['sources']['cover.png']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Replay assets changed'):
            store_product_version(self.root, receipt, plan)
        Path(receipt['sources']['cover.png']).write_bytes(b'cover')
        Path(receipt['evidence']['audit']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Evidence changed'):
            store_product_version(self.root, receipt, plan)
        other, manifest = self.fixture(product='other', job='other')
        (self.root / 'output/production/runs/other').symlink_to(self.root / 'sources', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink destination'):
            store_product_version(self.root, other, manifest)

    def test_recover_after_version_rename_and_export_index_failure(self):
        receipt, plan = self.fixture()
        from workflow import delivery_policy
        original = delivery_policy.write_json
        def interrupted(path, value):
            if Path(path).name == 'card.json': raise OSError('card interrupted')
            return original(path, value)
        with patch('workflow.delivery_policy.write_json', side_effect=interrupted):
            with self.assertRaises(OSError): store_product_version(self.root, receipt, plan)
        result = store_product_version(self.root, receipt, plan)
        self.assertEqual(result['version'], 1)
        self.assertEqual(len(self.card()['versions']), 1)
        repaired, manifest = self.fixture(job='film-new', content=b'new')
        def index_interrupted(path, value):
            if Path(path).name == 'delivery_index.json': raise OSError('index interrupted')
            return original(path, value)
        with patch('workflow.delivery_policy.write_json', side_effect=index_interrupted):
            with self.assertRaises(OSError): store_product_version(self.root, repaired, manifest)
        store_product_version(self.root, repaired, manifest)
        self.assertEqual(len(self.card()['versions']), 2)
        self.assertEqual(json.loads((self.root / 'exports/delivery_index.json').read_text())['items'][0]['version'], 2)

    def test_historical_import_never_grants_readiness(self):
        receipt, plan = self.fixture()
        store_product_version(self.root, receipt, plan, make_current=False, deliver=False)
        self.assertIsNone(self.card()['current_version'])
        self.assertFalse((self.root / 'exports').exists())
        # Fresh real validation appends an accepted revision, retaining history.
        store_product_version(self.root, receipt, plan)
        self.assertEqual(self.card()['current_version'], 2)
        self.assertEqual(len(self.card()['versions']), 2)

    def test_partial_copy_is_preserved_and_retry_completes(self):
        receipt, plan = self.fixture()
        def partial(source, target):
            Path(target).write_bytes(b'partial')
            raise OSError('copy interrupted')
        with patch('workflow.delivery_policy.shutil.copyfile', side_effect=partial):
            with self.assertRaises(OSError): store_product_version(self.root, receipt, plan)
        store_product_version(self.root, receipt, plan)
        self.assertEqual(len(self.card()['versions']), 1)
        retained = list((self.root / 'output/production/runs/film').rglob('.interrupted-*'))
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].read_bytes(), b'partial')

    def test_chain_parser_uses_full_suffix_and_explicit_ancestry(self):
        jid = 'film-ref-26003dcaa1-ref-009690a2-ref-76927e30b2-labels-7ff748c9-continuous-09312550'
        root, tokens = suffix_chain(jid)
        self.assertEqual(root, 'film'); self.assertEqual(len(tokens), 5)
        self.assertEqual(product_id(jid, {}), 'film')
        self.assertEqual(product_id('unrelated', {'unrelated': {'root_job_id': 'original'}}), 'original')
        self.assertEqual(suffix_chain('film-ref-deadbeef-unknown')[0], 'film-ref-deadbeef-unknown')

    def test_migration_dry_run_read_only_and_orphans_path_dependencies(self):
        for name in ['film', 'film-ref-deadbeef-labels-12345678']:
            folder = self.root / 'exports' / name; folder.mkdir(parents=True)
            (folder / 'video.mp4').write_bytes(name.encode())
            write_json(folder / 'manifest.json', {'id': name, 'root_job_id': 'film', 'title': '测试'})
        orphan = self.root / 'exports/orphan'; orphan.mkdir(); (orphan / 'video.mp4').write_bytes(b'orphan')
        empty = self.root / 'exports/evidence_only'; empty.mkdir(); (empty / 'proof.json').write_text('{}')
        production = self.root / 'output/production'; production.mkdir(parents=True)
        with sqlite3.connect(production / 'queue.sqlite') as db:
            db.executescript('CREATE TABLE jobs(id,manifest); CREATE TABLE tasks(id,result);')
            db.execute('INSERT INTO tasks VALUES(?,?)', ('task', json.dumps({'video_path': str(self.root / 'exports/film/video.mp4')})))
        before = {str(p): (p.stat().st_mtime_ns, sha256(p)) for p in self.root.rglob('*') if p.is_file()}
        with contextlib.redirect_stdout(io.StringIO()):
            report = main(['--root', str(self.root), '--dry-run'])
            default = main(['--root', str(self.root)])
        after = {str(p): (p.stat().st_mtime_ns, sha256(p)) for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(report['dry_run']); self.assertTrue(default['dry_run'])
        self.assertEqual(report['statistics']['chains'], 1)
        self.assertEqual(report['statistics']['orphan_directories'], 2)
        self.assertEqual(report['statistics']['versions'], 2)
        self.assertTrue(any(b['kind'] == 'live_path_reference' for b in report['blockers']))
        self.assertFalse((self.root / 'archives').exists())
        self.assertFalse((production / 'runs').exists())

    def test_dashboard_projects_history_and_reason_in_existing_template_fields(self):
        receipt, plan = self.fixture()
        store_product_version(self.root, receipt, plan, repair_reason='修复声音')
        production = self.root / 'output/production'
        with sqlite3.connect(production / 'queue.sqlite') as db:
            db.executescript('CREATE TABLE jobs(id,manifest); CREATE TABLE tasks(job_id,stage,result,status);')
            db.execute('INSERT INTO jobs VALUES(?,?)', (plan['id'], json.dumps(plan)))
        media = {'id': plan['id'], 'title': plan['title'], 'video_sha256': receipt['video_sha256']}
        with patch('workflow.products.catalog', return_value=[media]), patch('workflow.products.repair_requests', return_value=[]), patch('workflow.product_approval.approvals', return_value={}), patch('workflow.production_control.withdrawn', return_value=False):
            rows = products(production, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['repair_reason_chain'], ['修复声音'])
        self.assertIn('修复声音', rows[0]['versions'][0]['status'])
        self.assertEqual(rows[0]['current']['id'], plan['id'])
        self.assertEqual(rows[0]['current_version'], 1)
        self.assertEqual(len(read_cards(production)), 1)


class CardWritebackIntegration(legacy_writeback.RepairDeliveryWriteback):
    """Existing strict validator fixture; patch the new filesystem boundary."""
    def setUp(self):
        with patch.object(legacy_writeback.tempfile, 'TemporaryDirectory', KeptDirectory):
            super().setUp()

    def test_committed_database_recovers_filesystem_failure_without_duplicate(self):
        item, audit = self.fixture()
        with patch('workflow.repair_delivery.store_product_version', side_effect=OSError('disk interruption')):
            with self.assertRaises(OSError): self.finish(item, audit)
        self.finish(item, audit)
        with self.q.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM events').fetchone()[0], 1)
        card = json.loads((self.root / 'output/production/runs/p/card.json').read_text())
        self.assertEqual(len(card['versions']), 1)
        self.assertTrue((self.root / 'exports/delivery_index.json').exists())
