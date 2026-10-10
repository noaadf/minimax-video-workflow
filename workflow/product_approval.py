"""Version-bound human signoff; all queue admission/claims honor the product lock."""
import json,time
from pathlib import Path

def initialize(c):
    c.execute('CREATE TABLE IF NOT EXISTS product_approvals(product_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,video_sha256 TEXT NOT NULL,video_path TEXT NOT NULL,approved_at REAL NOT NULL)')

def identity(c,jid,plan=None):
    from .products import product_id
    plans={r[0]:json.loads(r[1]) for r in c.execute('SELECT id,manifest FROM jobs')}
    if plan is not None:plans[jid]=plan
    return product_id(jid,plans)

def locked(c,jid,plan=None):
    initialize(c)
    return c.execute('SELECT * FROM product_approvals WHERE product_id=?',(identity(c,jid,plan),)).fetchone()

def approvals(c):
    initialize(c)
    return {r[0]:dict(zip(('product_id','job_id','video_sha256','video_path','approved_at'),r)) for r in c.execute('SELECT * FROM product_approvals')}

def approve(root,jid,sha):
    import sqlite3
    from .dashboard_actions import current,ActionError
    from .dashboard import snapshot
    from .products import products
    # Require current full-film assessment, not merely existence of an MP4.
    item=current(root,jid,sha)
    product=next((p for p in products(root,snapshot(root)['jobs']) if p['current'] and p['current']['id']==jid),None)
    if not product or not product['can_approve']:raise ActionError('这条视频尚未完成迭代，或已定稿。请刷新查看状态。')
    with sqlite3.connect(Path(root)/'queue.sqlite',timeout=30) as c:
        c.execute('BEGIN IMMEDIATE');initialize(c)
        key=identity(c,jid)
        if locked(c,jid):raise ActionError('作品已定稿。')
        family=[r[0] for r in c.execute('SELECT id FROM jobs') if identity(c,r[0])==key]
        for member in family:
            if c.execute("SELECT 1 FROM tasks WHERE job_id=? AND status IN ('pending','running','reconcile')",(member,)).fetchone():raise ActionError('还有任务正在处理，请完成后审批。')
        if any(r[1] in ('pending','running','processing') and identity(c,r[0])==key for r in c.execute('SELECT job_id,status FROM user_repair_requests')):raise ActionError('还有返修待处理，请完成后审批。')
        # Recheck bytes immediately before committing the signoff.
        from .core.io import sha256
        if sha256(Path(item['video']))!=sha:raise ActionError('视频已变化，请刷新。')
        c.execute('INSERT INTO product_approvals VALUES(?,?,?,?,?)',(key,jid,sha,item['video'],time.time()))
        c.execute('INSERT INTO events(at,task_id,event,detail) VALUES(?,?,?,?)',(time.time(),jid,'product_approved',json.dumps({'product_id':key,'video_sha256':sha})))
    return {'message':'已审批定稿，当前完整成片已锁定，停止后续自动迭代。','status':'approved'}
