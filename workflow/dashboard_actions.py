"""Local dashboard exports and version-bound user repair requests."""
import fcntl,json,re,shutil,sqlite3,tempfile,time,uuid
from pathlib import Path
from .core.io import sha256, write_json

class ActionError(ValueError):pass


def catalog(root):
    root=Path(root).resolve();rows={}
    db=root/'queue.sqlite'
    if db.exists():
        with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as c:
            for jid,manifest,result in c.execute("SELECT j.id,j.manifest,t.result FROM jobs j JOIN tasks t ON t.job_id=j.id WHERE t.stage='export' AND t.status='succeeded'"):
                result=json.loads(result or '{}');plan=json.loads(manifest)
                if result.get('video_path'):rows[jid]={'id':jid,'title':plan.get('title',jid),'video':result['video_path']}
    for bundle in (root).glob('*/latest_review_bundle.json'):
        for item in json.loads(bundle.read_text(encoding="utf-8")).get('videos',[]):
            rows[item['id']]={**rows.get(item['id'],{}),**item}
    if db.exists():
        from .product_approval import approvals
        with sqlite3.connect(db) as c:
            for approval in approvals(c).values():
                jid=approval['job_id']
                rows[jid]={**rows.get(jid,{'id':jid,'title':jid}),'video':approval['video_path']}
    from .production_control import withdrawn
    plans={}
    if db.exists():
        with sqlite3.connect(db) as c:plans={jid:json.loads(raw) for jid,raw in c.execute('SELECT id,manifest FROM jobs')}
    rows={jid:item for jid,item in rows.items() if not withdrawn(root,jid,plans)}
    out=[]
    for item in rows.values():
        video=Path(item['video']).resolve()
        if not video.is_file() or not (video.is_relative_to(root) or video.is_relative_to(root.parent.parent/'exports')):continue
        parent=video.parent
        if not item.get('title') and (parent/'manifest.json').exists():
            item['title']=json.loads((parent/'manifest.json').read_text(encoding="utf-8")).get('title',item['id'])
        out.append({'id':item['id'],'title':item.get('title',item['id']),'video':str(video),'video_sha256':sha256(video)})
    return out


def current(root,job_id,expected):
    item=next((x for x in catalog(root) if x['id']==job_id),None)
    if not item:raise ActionError('这条视频尚未生成完整成片。')
    from .product_approval import locked
    with sqlite3.connect(Path(root)/'queue.sqlite') as c:
        c.row_factory=sqlite3.Row
        approval=locked(c,job_id)
        if approval and (approval['job_id']!=job_id or approval['video_sha256']!=item['video_sha256']):raise ActionError('此作品已定稿，请导出已批准且未改变的版本。')
    if expected!=item['video_sha256']:raise ActionError('视频版本已更新，请刷新后再操作。')
    return item


def preferences(root):
    p=Path(root)/'dashboard_preferences.json'
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {'export_folder':str(Path.home()/'Desktop/短视频待上传')}


def export_film(root,job_id,expected,destination):
    root=Path(root);item=current(root,job_id,expected)
    if not isinstance(destination,str) or not destination.strip() or len(destination)>2000:raise ActionError('请填写导出文件夹的完整路径。')
    target=Path(destination.strip()).expanduser()
    if not target.is_absolute():raise ActionError('请填写以 / 开头的完整路径，或使用 ~/。')
    target=target.resolve();target.mkdir(parents=True,exist_ok=True)
    title=re.sub(r'[^\w\u4e00-\u9fff -]','_',item['title'])[:48].strip() or '短视频'
    name=title+'_'+re.sub(r'[^\w-]','_',job_id)[-24:]+'_'+expected[:10]
    final=target/name
    with (root/'dashboard_export.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if final.is_symlink():raise ActionError('目标子文件夹是链接，请更换导出位置。')
        existing=final/'导出清单.json'
        if final.exists():
            if not existing.is_file():raise ActionError('目标已有同名文件夹，请更换位置。')
            receipt=json.loads(existing.read_text(encoding="utf-8"))
            if receipt.get('video_sha256')!=expected or any(not (final/n).is_file() or sha256(final/n)!=h for n,h in receipt['files'].items()):
                raise ActionError('已导出文件有变化，请更换位置以保留已有文件。')
        else:
            staging=Path(tempfile.mkdtemp(prefix='.video-export-',dir=target))
            try:
                parent=Path(item['video']).parent;files={}
                mapping={'video.mp4':'视频.mp4','cover.png':'封面.png','publication.txt':'发布文案.txt','pinned_comment_draft.txt':'置顶评论.txt'}
                for source,name_out in mapping.items():
                    src=Path(item['video']) if source=='video.mp4' else parent/source
                    if not src.is_file():continue
                    if not (src.resolve().is_relative_to(root.resolve()) or src.resolve().is_relative_to(root.resolve().parent.parent/'exports')):raise ActionError('交付文件路径无效。')
                    shutil.copyfile(src,staging/name_out);files[name_out]=sha256(staging/name_out)
                if files.get('视频.mp4')!=expected:raise ActionError('视频复制校验失败，请重试。')
                receipt={'job_id':job_id,'video_sha256':expected,'files':files,'exported_at':time.time()}
                write_json(staging/'导出清单.json',receipt);staging.rename(final)
            finally:
                if staging.exists():shutil.rmtree(staging)
        write_json(root/'dashboard_preferences.json',{'export_folder':str(target)})
    return {'folder':str(final),'files':list(receipt['files']),'message':'已导出视频、封面及可用文案。'}


def repair_requests(root):
    with sqlite3.connect(Path(root)/'queue.sqlite') as c:
        c.row_factory=sqlite3.Row
        c.execute('CREATE TABLE IF NOT EXISTS user_repair_requests(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,video_sha256 TEXT NOT NULL,video_path TEXT NOT NULL,note TEXT NOT NULL,time_seconds REAL,status TEXT NOT NULL,created_at REAL NOT NULL)')
        return [dict(r) for r in c.execute('SELECT * FROM user_repair_requests ORDER BY created_at DESC LIMIT 100')]


def request_repair(root,job_id,expected,note,time_seconds=None,request_id=None):
    item=current(root,job_id,expected)
    if not isinstance(note,str) or not 1<=len(note.strip())<=3000:raise ActionError('请填写1–3000字的问题说明。')
    manifest=Path(item['video']).parent/'manifest.json'
    length=json.loads(manifest.read_text(encoding="utf-8")).get('duration_seconds',30) if manifest.exists() else 30
    if time_seconds is not None and (type(time_seconds) not in (int,float) or not 0<=time_seconds<=length):raise ActionError(f'视频时间须在0–{length}秒之间。')
    try:rid=str(uuid.UUID(request_id)) if request_id else str(uuid.uuid4())
    except (ValueError,TypeError):raise ActionError('提交标识无效，请刷新重试。')
    repair_requests(root)
    with sqlite3.connect(Path(root)/'queue.sqlite') as c:
        c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE')
        from .product_approval import locked
        if locked(c,job_id):raise ActionError('此作品已审批定稿，停止返修。')
        old=c.execute('SELECT * FROM user_repair_requests WHERE id=?',(rid,)).fetchone()
        if old:
            if (old['job_id'],old['video_sha256'],old['note'],old['time_seconds'])!=(job_id,expected,note.strip(),time_seconds):raise ActionError('重复提交内容不一致，请重新打开返修窗口。')
        else:
            c.execute('INSERT INTO user_repair_requests VALUES(?,?,?,?,?,?,?,?)',(rid,job_id,expected,item['video'],note.strip(),time_seconds,'pending',time.time()))
            c.execute('INSERT INTO events(at,task_id,event,detail) VALUES(?,?,?,?)',(time.time(),job_id,'user_repair_requested',json.dumps({'request_id':rid,'note':note.strip(),'time_seconds':time_seconds},ensure_ascii=False)))
    return {'request_id':rid,'status':'pending','message':'返修已登记，等待处理；当前视频和旧版均保留。'}
