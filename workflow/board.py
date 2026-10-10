"""Board projection: three lanes (finished / waiting / unstarted material) + publish action."""
import json, sqlite3, time
from pathlib import Path
from .production_control import settings


def material_items(root):
    """Material intakes that have not finished their production run."""
    with sqlite3.connect(Path(root) / 'queue.sqlite') as c:
        rows = c.execute('SELECT content_id, state, detail, updated FROM material_intakes ORDER BY updated DESC').fetchall()
    items = [{'id': r[0], 'state': r[1], 'detail': r[2][:200], 'updated': r[3]} for r in rows
             if r[1] not in ('withdrawn', 'completed')]
    # User submissions awaiting editorial review ride the same lane.
    from .material_submission import submissions
    for record in submissions(root):
        if record.get('state') not in ('withdrawn', 'completed'):
            items.append({'id': record['content_id'], 'state': record.get('state', 'pending_editorial_review'),
                          'detail': (record.get('title') or '') + (f" · {record['chars']} 字" if record.get('chars') else ''),
                          'updated': record.get('received', 0)})
    return items


def snapshot(root):
    """Lanes for the board page. Finished products hide once published."""
    from .dashboard import snapshot as dash_snapshot
    from .products import products
    root = Path(root)
    prods = products(root, dash_snapshot(root)['jobs'])
    done = [p for p in prods if p['current'] and p['active_count'] == 0
            and not any(r['status'] in ('pending', 'running', 'processing') for r in p['requests'])]
    waiting = [p for p in prods if p not in done]
    published = sorted(settings(root).get('published_products', []))
    done = [p for p in done if p['id'] not in published]
    return {'at': time.time(), 'done': done, 'waiting': waiting,
            'materials': material_items(root), 'published': published}


def publish(root, product_id):
    """Mark a finished product as published: it leaves the board (reversible),
    and the pipeline stops automatic repair iterations for it."""
    if not product_id or not isinstance(product_id, str):
        raise ValueError('缺少产品编号。')
    root = Path(root)
    from .dashboard import snapshot as dash_snapshot
    from .products import products
    known = {p['id'] for p in products(root, dash_snapshot(root)['jobs'])}
    if product_id not in known:
        raise ValueError('未知产品编号，拒绝记账。')
    cfg = settings(root)
    published = set(cfg.get('published_products', []))
    if product_id not in published:
        published.add(product_id)
        cfg['published_products'] = sorted(published)
        cfg['withdrawn_products'] = sorted(set(cfg.get('withdrawn_products', [])) | {product_id})
        cfg['published_at'] = {**cfg.get('published_at', {}), product_id: time.time()}
        path = root / 'production_control.json'
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    return {'ok': True, 'product_id': product_id, 'published': sorted(published)}
