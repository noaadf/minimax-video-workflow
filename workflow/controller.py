"""Durable, bounded execution of admitted films and exact-parent repair recipes.

This controller does not author new references or certify publication readiness.
Recipes are immutable manifests, reviewed before admission, not shell commands.
"""
import fcntl
import json
import time
from contextlib import contextmanager
from pathlib import Path

from .core.io import digest, sha256, write_json
from .repair import job_bundle
from .runner import submit
from .backends import assignments, check_lease

TERMINAL = {'quarantined', 'ready_for_review'}


class Controller:
    def __init__(self, queue, root, submitter=submit, bundle=job_bundle):
        self.queue = queue
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.submitter, self.bundle = submitter, bundle
        with queue.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS production_batches(
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, config TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS production_items(
                    batch_id TEXT NOT NULL, item_id TEXT NOT NULL, current_job TEXT NOT NULL,
                    state TEXT NOT NULL, round INTEGER NOT NULL DEFAULT 0,
                    spec TEXT NOT NULL, intent TEXT, detail TEXT NOT NULL DEFAULT '{}',
                    updated REAL NOT NULL, PRIMARY KEY(batch_id,item_id));
            ''')

    @contextmanager
    def locked(self):
        # Lock belongs to the queue, not the chosen output directory.
        with open(self.queue.path + '.controller.lock', 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def admit(self, config):
        """Adopt existing jobs. Initial authoring/submission stays in batch.produce."""
        if not config.get('id') or not config.get('items'):
            raise ValueError('batch id and items required')
        identities = [x['id'] for x in config['items']]
        if len(set(identities)) != len(identities):
            raise ValueError('duplicate item')
        with self.locked(), self.queue.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT digest FROM production_batches WHERE id=?', (config['id'],)).fetchone()
            if old:
                if old['digest'] != digest(config):
                    raise ValueError('admitted batch is immutable; use a new batch id')
                return False
            jobs = [x['job_id'] for x in config['items']]
            if len(set(jobs)) != len(jobs):
                raise ValueError('one item per job')
            for item in config['items']:
                if not c.execute('SELECT 1 FROM jobs WHERE id=?', (item['job_id'],)).fetchone():
                    raise ValueError('admission requires an existing queued job')
                if c.execute('SELECT 1 FROM production_items WHERE current_job=?', (item['job_id'],)).fetchone():
                    raise ValueError('job already owned by a controller item')
                budget = item.get('max_repairs', 2)
                if type(budget) is not int or not 0 <= budget <= 3:
                    raise ValueError('max_repairs must be 0..3')
                parents = set()
                for recipe in item.get('recipes', []):
                    if recipe['parent_job_id'] in parents:
                        raise ValueError('ambiguous repair recipe')
                    parents.add(recipe['parent_job_id'])
                    if recipe.get('reviewed') is not True:
                        raise ValueError('only reviewed recipes may be admitted')
                    if sha256(recipe['manifest_path']) != recipe['sha256']:
                        raise ValueError('recipe manifest changed')
            c.execute('INSERT INTO production_batches VALUES(?,?,?)',
                      (config['id'], digest(config), json.dumps(config)))
            for item in config['items']:
                c.execute('INSERT INTO production_items(batch_id,item_id,current_job,state,spec,updated) VALUES(?,?,?,?,?,?)',
                          (config['id'], item['id'], item['job_id'], 'watching', json.dumps(item), time.time()))
            self.queue.event(c, config['id'], 'controller_admitted', str(len(jobs)))
        return True

    def items(self):
        with self.queue.connect() as c:
            return [dict(row) for row in c.execute('SELECT * FROM production_items ORDER BY batch_id,item_id')]

    def add_recipe(self, batch_id, item_id, recipe, *, diagnosis=None):
        """Resume a content quarantine with new reviewed evidence, retaining the spent budget."""
        with self.locked(), self.queue.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM production_items WHERE batch_id=? AND item_id=?',
                            (batch_id, item_id)).fetchone()
            if not row:
                raise ValueError('unknown controller item')
            spec, detail = json.loads(row['spec']), json.loads(row['detail'])
            diagnosed = diagnosis is not None
            expected = 'repair_budget_exhausted' if diagnosed else 'no_validated_repair_recipe'
            if row['state'] != 'quarantined' or detail.get('reason') != expected:
                raise ValueError('only missing-recipe quarantine can resume; reconcile execution failures separately')
            if diagnosed:
                # Explicit, evidence-bound corrective admission; never called by tick/retry.
                if spec.get('diagnosed_correction') or row['round'] >= 3:
                    raise ValueError('diagnosed correction already spent or absolute budget exhausted')
                if row['round'] != spec.get('max_repairs', 2):
                    raise ValueError('diagnosed correction requires exhausted current budget')
                if diagnosis.get('parent_job_id') != row['current_job'] or not diagnosis.get('reason'):
                    raise ValueError('diagnosis requires exact parent and reason')
                evidence = diagnosis.get('evidence', [])
                if not evidence or any(sha256(e['path']) != e['sha256'] for e in evidence):
                    raise ValueError('diagnosis evidence missing or changed')
                spec['diagnosed_correction'] = diagnosis
                spec['max_repairs'] = row['round'] + 1
            elif row['round'] >= spec.get('max_repairs', 2):
                raise ValueError('repair budget exhausted')
            if recipe.get('parent_job_id') != row['current_job'] or recipe.get('reviewed') is not True:
                raise ValueError('new recipe requires review and exact current parent')
            if sha256(recipe['manifest_path']) != recipe['sha256']:
                raise ValueError('recipe manifest changed')
            if any(r['parent_job_id'] == row['current_job'] for r in spec.get('recipes', [])):
                raise ValueError('recipe already exists for current parent')
            spec.setdefault('recipes', []).append(recipe)
            c.execute("UPDATE production_items SET state='watching',spec=?,detail=?,updated=? WHERE batch_id=? AND item_id=?",
                      (json.dumps(spec), json.dumps({'action': 'reviewed_recipe_added'}), time.time(), batch_id, item_id))
            self.queue.event(c, row['current_job'], 'controller_recipe_added', json.dumps(recipe))
            if diagnosed:
                self.queue.event(c, row['current_job'], 'controller_diagnosed_correction', json.dumps(diagnosis))

    def change(self, item, state, detail, **fields):
        with self.queue.connect() as c:
            c.execute('UPDATE production_items SET state=?,detail=?,updated=?,current_job=?,round=?,intent=? WHERE batch_id=? AND item_id=?',
                      (state, json.dumps(detail), time.time(), fields.get('current_job', item['current_job']),
                       fields.get('round', item['round']), fields.get('intent', item['intent']),
                       item['batch_id'], item['item_id']))
            self.queue.event(c, item['current_job'], 'controller_' + state, json.dumps(detail))

    def dispatch(self, item):
        """Recover the same committed intent even after submit succeeds but state save fails."""
        intent = json.loads(item['intent'])
        with self.queue.connect() as c:
            existing = c.execute('SELECT manifest FROM jobs WHERE id=?', (intent['job_id'],)).fetchone()
        if existing:
            if json.loads(existing['manifest']).get('controller_intent') != intent['key']:
                raise ValueError('repair job id occupied by a different intent')
        else:
            if sha256(intent['manifest_path']) != intent['sha256']:
                raise ValueError('prepared repair manifest changed')
            plan = json.loads(Path(intent['manifest_path']).read_text(encoding="utf-8"))
            for group in assignments(intent['backend'], plan['shots']).values():
                check_lease(group, require_expiry=True)
            self.submitter(self.queue, intent['manifest_path'], self.root, intent['backend'])
        self.change(item, 'watching', {'action': 'repair_enqueued', 'parent': item['current_job']},
                    current_job=intent['job_id'], round=item['round'] + 1, intent=None)

    def prepare(self, item, recipe, parent):
        if sha256(recipe['manifest_path']) != recipe['sha256']:
            raise ValueError('recipe manifest changed')
        p = json.loads(Path(recipe['manifest_path']).read_text(encoding="utf-8"))
        if p.get('parent_job_id') != item['current_job'] or p['id'] == item['current_job']:
            raise ValueError('recipe does not match exact parent')
        # This first version handles visual/audio-stem repairs, not factual/script rewrites.
        for field in ('facts', 'narration'):
            if p.get(field) != parent.get(field):
                raise ValueError('recipe changes protected ' + field)
        key = digest({'batch': item['batch_id'], 'item': item['item_id'],
                      'round': item['round'] + 1, 'recipe': recipe})
        p['controller_intent'] = key
        path = self.root / 'controller' / (key + '.json')
        write_json(path, p)
        intent = {'key': key, 'job_id': p['id'], 'manifest_path': str(path),
                  'sha256': sha256(path), 'backend': recipe.get('backend', {'type': 'assets'})}
        # Intent is durable before any enqueue/external execution.
        self.change(item, 'submitting', {'action': 'prepared_repair'}, intent=json.dumps(intent))
        self.dispatch({**item, 'intent': json.dumps(intent)})

    def tick(self):
        with self.locked():
            for item in self.items():
                if item['state'] in TERMINAL:
                    continue
                try:
                    self.advance(item)
                except Exception as e:
                    # Quarantine one item, continue others. Uncertain remote work is never resubmitted.
                    self.change(item, 'quarantined', {'reason': 'controller_error', 'error': str(e)[:500]})
            return self.snapshot()

    def advance(self, item):
        from .product_approval import locked
        with self.queue.connect() as c:
            if locked(c,item['current_job']):
                return
        if item['state'] == 'submitting':
            self.dispatch(item)
            return
        spec = json.loads(item['spec'])
        with self.queue.connect() as c:
            tasks = [dict(r) for r in c.execute('SELECT * FROM tasks WHERE job_id=?', (item['current_job'],))]
        failed = [t for t in tasks if t['status'] in ('failed', 'blocked', 'reconcile')]
        if failed:
            self.change(item, 'quarantined', {'reason': 'execution_failed_or_uncertain',
                        'tasks': [{'id': t['id'], 'status': t['status'], 'error': t['error']} for t in failed]})
            return
        if not tasks:
            raise ValueError('job has no tasks')
        if any(t['status'] != 'succeeded' for t in tasks):
            return
        plan, results = self.bundle(self.queue, item['current_job'])
        quality = results.get('quality', {})
        blockers = spec.get('content_blockers', [])
        # Root visual findings are independent evidence. A model pass cannot erase them.
        review = quality.get('editorial', {}).get('review', {})
        accepted = (quality.get('passed') is True and
                    quality.get('editorial_status') == 'automatic_review_passed' and
                    review.get('verdict') == 'pass' and not blockers)
        policy=quality.get('delivery_policy',{})
        if policy.get('profile')=='throughput_v1' and policy.get('ready_for_delivery') is True and not blockers:
            self.change(item,'ready_for_review',{'technical_passed':True,'export':results.get('export'),
                        'publication_approved':False,'delivery_policy':policy})
            return
        recipes = [r for r in spec.get('recipes', []) if r['parent_job_id'] == item['current_job']]
        repair_limit=spec.get('max_repairs',2)
        if plan.get('quality_profile')=='throughput_v1':repair_limit=min(repair_limit,1)
        # Explicit reviewed correction (e.g. removing unauditioned foley) takes precedence over a model pass.
        if recipes and item['round'] < repair_limit:
            self.prepare(item, recipes[0], plan)
            return
        if accepted:
            self.change(item, 'ready_for_review', {'technical_passed': True,
                        'export': results.get('export'), 'publication_approved': False,
                        'limitations': quality.get('unverified', [])})
            return
        self.change(item, 'quarantined', {
            'reason': 'repair_budget_exhausted' if item['round'] >= repair_limit else 'no_validated_repair_recipe',
            'technical_passed': quality.get('passed') is True,
            'editorial_status': quality.get('editorial_status', 'unverified'),
            'content_blockers': blockers, 'findings': review.get('findings', []),
            'export': results.get('export'), 'publication_approved': False})

    def snapshot(self):
        rows = self.items()
        report = {'at': time.time(), 'scope': 'admitted jobs and reviewed exact-parent repair recipes',
                  'publication_approved': False, 'items': [
                      {k: json.loads(v) if k == 'detail' else v for k, v in row.items() if k not in ('spec', 'intent')}
                      for row in rows]}
        # A single writer holds controller.lock. Replace prevents partial dashboard reads.
        path = self.root / 'controller_status.json'
        temp = path.with_suffix('.tmp')
        write_json(temp, report)
        temp.replace(path)
        return report

    def active_jobs(self):
        return [r['current_job'] for r in self.items() if r['state'] not in TERMINAL]
