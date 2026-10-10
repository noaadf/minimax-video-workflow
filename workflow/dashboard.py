"""Local production monitor with version-bound export and repair controls."""
import argparse
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import secrets
import threading
import time
import time
from collections import Counter
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit


def style_catalog(root):
    root=Path(root).resolve()
    path=root/'style_library/catalog.json'
    if not path.is_file():return {'items':[]}
    data=json.loads(path.read_text(encoding="utf-8"));items=[]
    for entry in data.get('items',[]):
        item={k:v for k,v in entry.items() if not k.endswith('_path')}
        for key in ('audio','video','cover','publication'):
            if entry.get(key+'_path'):
                file=Path(entry[key+'_path']).resolve()
                if file.is_relative_to(root/'style_library') and file.is_file():
                    item[key+'_url']='/media/'+quote(str(file.relative_to(root)),safe='/')
        items.append(item)
    return {'items':items}


def review_packet_catalog(root):
    root=Path(root).resolve(); folder=root/'review_packets'; index=folder/'catalog.json'
    if not index.is_file(): return {'media_urls':[]}
    data=json.loads(index.read_text(encoding="utf-8")); urls=[]
    for path in data.get('media_paths',[])+data.get('speech_media_paths',[])+data.get('motion_media_paths',[]):
        file=Path(path).resolve()
        if file.is_relative_to(folder) and file.is_file() and file.suffix in ('.mp4','.mp3','.json'):
            urls.append('/media/'+quote(str(file.relative_to(root)),safe='/'))
    result={'media_urls':urls,'content_approved':False}
    for key in ('index_path', 'speech_index_path', 'motion_index_path'):
        page=Path(data.get(key,'')).resolve()
        if page.is_relative_to(folder) and page.is_file() and page.suffix=='.html':
            result[key]=str(page)
    acceptance=Path(data.get('acceptance_index_path','')).resolve()
    bundle=root/'pilot_six_20261001/latest_review_bundle.json'
    if (acceptance.is_relative_to(folder) and acceptance.is_file() and acceptance.suffix=='.html'
            and bundle.is_file() and hashlib.sha256(bundle.read_bytes()).hexdigest()==data.get('acceptance_bundle_sha256')):
        result['acceptance_index_path']=str(acceptance)
    return result


def _snapshot_queue(root, database):
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN')
        jobs = connection.execute('SELECT * FROM jobs').fetchall()
        from .production_control import withdrawn
        plans={j['id']:json.loads(j['manifest']) for j in jobs}
        jobs=[j for j in jobs if not withdrawn(root,j['id'],plans)]
        tasks = connection.execute('SELECT * FROM tasks ORDER BY rowid').fetchall()
        events = connection.execute('SELECT * FROM events ORDER BY seq').fetchall()
    return jobs, tasks, events


def _snapshot_task_details(task, result, job_events, now, media_link):
    claims = [e['at'] for e in job_events if e['task_id'] == task['id'] and e['event'] == 'claimed']
    active_elapsed = now - claims[-1] if claims and task['status'] == 'running' else None
    data = json.loads(task['payload'])['data']
    d = {'id': task['id'], 'stage': task['stage'], 'status': task['status'],
         'attempts': task['attempts'], 'error': task['error'], 'capability':task['capability'],
         'elapsed_seconds': result.get('elapsed_seconds', active_elapsed),
         'heartbeat_age_seconds': max(0, now - (task['lease_until'] - 120)) if task['lease_until'] else None,
         'camera': data.get('camera'), 'duration_seconds': data.get('duration_seconds'),
         'video_url': media_link(result.get('video_path')),
         'audio_url': media_link(result.get('audio_path'))}
    return d


def _snapshot_tasks(rows, job_events, now, media_link, generation_times, finished):
    details = []
    results = {}
    for task in rows:
        result = json.loads(task['result']) if task['result'] else {}
        results[task['stage']] = result
        d = _snapshot_task_details(task, result, job_events, now, media_link)
        if task['stage'] == 'shot' and result.get('metrics', {}).get('reused_asset') is not True and result.get('elapsed_seconds'):
            generation_times.append(result['elapsed_seconds'])
        details.append(d)
    counts = Counter(t['status'] for t in rows)
    status = ('completed' if finished else 'attention' if any(counts[x] for x in ('failed', 'blocked', 'reconcile'))
              else 'running' if counts['running'] else 'queued')
    return details, results, counts, status


def _snapshot_progress(rows, job_events, now):
    started = next((e['at'] for e in job_events if e['event'] == 'submitted'), now)
    finished = all(t['status'] == 'succeeded' for t in rows)
    # Completed jobs keep their last event time across dashboard refreshes.
    done_at = max((e['at'] for e in job_events), default=started) if finished else None
    return started, finished, done_at


def _snapshot_record(job, plan, rows, job_events, now, media_link, generation_times):
    started, finished, done_at = _snapshot_progress(rows, job_events, now)
    details, results, counts, status = _snapshot_tasks(rows, job_events, now, media_link, generation_times, finished)
    quality = results.get('quality', {})
    export = results.get('export', {})
    compose = results.get('compose', {})
    record = {'id': job['id'], 'title': plan.get('title', job['id']), 'status': status,
              'created_at': started, 'finished_at': done_at,
              'elapsed_seconds': (done_at or now) - started, 'counts': dict(counts),
              'shot_done': sum(t['stage'] == 'shot' and t['status'] == 'succeeded' for t in rows),
              'shot_total': sum(t['stage'] == 'shot' for t in rows), 'tasks': details,
              'video_url': media_link(export.get('video_path') or compose.get('video_path')),
              'cover_url': media_link(export.get('cover_path') or compose.get('cover_path')),
              'publication_url': media_link(export.get('publication_path')),
              'quality_url': media_link(quality.get('report_path')),
              'editorial_status': quality.get('editorial_status'), 'technical_checks': quality.get('checks', {}),
              'findings': quality.get('editorial', {}).get('review', {}).get('findings', []),
              'comment_prompt': plan.get('comment_prompt'), 'narration': plan.get('narration', []),
              'facts': plan.get('facts', []), 'repair_round': plan.get('repair_round', 0),
              'parent_job_id': plan.get('parent_job_id'),
              'new_generation': any(not s.get('source_path') for s in plan.get('shots', [])),
              'music_note': plan.get('music', {}).get('license_note'),
              'backend': plan.get('backend', {}).get('type'),
              'review_model': quality.get('editorial', {}).get('receipt', {}).get('requested_model')}
    return record


def _snapshot_records(jobs, tasks, events, now, media_link):
    records = []
    generation_times = []
    for job in jobs:
        plan = json.loads(job['manifest'])
        rows = [t for t in tasks if t['job_id'] == job['id']]
        job_events = [e for e in events if e['task_id'] == job['id'] or e['task_id'] in {t['id'] for t in rows}]
        record = _snapshot_record(job, plan, rows, job_events, now, media_link, generation_times)
        records.append(record)
    return records, generation_times


def _snapshot_estimate(job, average):
    unfinished_shots = [t for t in job['tasks'] if t['stage'] == 'shot' and t['status'] != 'succeeded']
    group_loads={}
    for task in unfinished_shots:
        group=task['capability']
        group_loads[group]=group_loads.get(group,0)+max(0,(average or 130)-(task['elapsed_seconds'] or 0))
    estimate=max(group_loads.values(),default=0)
    # Independent groups overlap; other jobs and retries can extend this approximate estimate.
    estimate += 180 if not job['video_url'] else 60
    job['eta_seconds_estimate'] = estimate if job['status'] in ('queued', 'running') else None
    job['eta_basis'] = '按独立设备组剩余负载估算 + 后期预留；其他任务竞争和重画另计' if average else '4 秒样片基线 + 后期预留，尚未实测整片'

def _snapshot_derivatives(records, derivatives, media_link):
    for record in records:
        derivative = derivatives.get(record['id'], {})
        link = media_link(derivative.get('video'))
        if link and derivative.get('programmatic_passed') is True and derivative.get('audio_packets_unchanged') is True:
            record['original_video_url'] = record['video_url']
            record['video_url'] = link
            record['editorial_status'] = 'review_required'
            record['postproduction'] = {'kind': derivative.get('kind', 'disclosure_overlay_correction'),
                'content_approved': False, 'evidence_url': media_link(derivative.get('receipt'))}


def _snapshot_summary(records, tasks, average, generation_times):
    return {'running': sum(j['status'] == 'running' for j in records),
            'completed': sum(j['status'] == 'completed' for j in records),
            'approved': sum(j['editorial_status'] == 'automatic_review_passed' for j in records),
            'attention': sum(j['status'] == 'attention' or j['editorial_status'] == 'review_required' for j in records),
            # Queue-wide device load includes tasks from filtered-out jobs.
            'active_generation_groups':len({t['capability'] for t in tasks if t['stage']=='shot' and t['status']=='running' and t['capability']!='cpu'}),
            'generation_seconds_mean': average, 'generation_samples': len(generation_times)}


def snapshot(root):
    root = Path(root).resolve()
    now = time.time()
    database = root / 'queue.sqlite'
    if not database.is_file():
        return {'at': now, 'jobs': [], 'summary': {}, 'events': []}
    jobs, tasks, events = _snapshot_queue(root, database)
    def media_link(path):
        if not path:
            return None
        path = Path(path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        return '/media/' + quote(str(path.relative_to(root)), safe='/')
    records, generation_times = _snapshot_records(jobs, tasks, events, now, media_link)
    average = sum(generation_times) / len(generation_times) if generation_times else None
    for job in records:
        _snapshot_estimate(job, average)
    records.sort(key=lambda j: j['created_at'], reverse=True)
    planning_path = root / 'live_control.json'
    planning = json.loads(planning_path.read_text(encoding="utf-8")) if planning_path.is_file() else None
    # CPU-only derivatives keep their production owner and original queue history.
    derivatives_path = root / 'postproduction_artifacts.json'
    derivatives = json.loads(derivatives_path.read_text(encoding="utf-8")) if derivatives_path.is_file() else {}
    _snapshot_derivatives(records, derivatives, media_link)
    return {'at': now, 'jobs': records, 'control': planning,
            'summary': _snapshot_summary(records, tasks, average, generation_times),
            'events': [{'at': e['at'], 'task_id': e['task_id'], 'event': e['event'], 'detail': e['detail'][:500]}
                       for e in reversed(events[-60:])]}


def make_handler(root):
    root = Path(root).resolve()
    action_token=secrets.token_urlsafe(32)
    media_cache={'at':0.0,'items':[],'refreshing':False}  # catalog/board rebuilds take seconds and hold the GIL in
    board_cache={'at':0.0,'data':None,'refreshing':False}  # bursts; serve stale instantly and rebuild in background,
    products_cache={'at':0.0,'data':None,'refreshing':False}  # or video range requests stall mid-playback.
    allow_cache={'at':0.0,'urls':set(),'refreshing':False}
    def _bg(cache, rebuild):  # or video range requests stall mid-playback.
        if cache.get('refreshing'):return
        cache['refreshing']=True
        def run():
            try:
                result=rebuild()
                if result is not None:
                    cache['items' if 'items' in cache else 'data']=result;cache['at']=time.time()
            finally:cache['refreshing']=False
        threading.Thread(target=run,daemon=True).start()
    def catalog_cached(ttl=120.0):
        now=time.time()
        if not media_cache['items']:
            from .dashboard_actions import catalog
            media_cache['items']=catalog(root);media_cache['at']=time.time()
        elif now-media_cache['at']>ttl:_bg(media_cache,lambda:__import__('workflow.dashboard_actions',fromlist=['catalog']).catalog(root))
        return media_cache['items']
    def board_cached(ttl=60.0):
        now=time.time()
        if board_cache['data'] is None:
            from .board import snapshot as board_snapshot
            board_cache['data']=board_snapshot(root);board_cache['at']=time.time()
        elif now-board_cache['at']>ttl:_bg(board_cache,lambda:__import__('workflow.board',fromlist=['snapshot']).snapshot(root))
        return board_cache['data']
    def products_cached(ttl=60.0):
        now=time.time()
        if products_cache['data'] is None:
            from .products import products as products_fn
            from .pipeline_status import snapshot as pipeline_snapshot
            products_cache['data']=products_fn(root,pipeline_snapshot(root)['jobs']);products_cache['at']=time.time()
        elif now-products_cache['at']>ttl:
            def rebuild():
                from .products import products as products_fn
                from .pipeline_status import snapshot as pipeline_snapshot
                return products_fn(root,pipeline_snapshot(root)['jobs'])
            _bg(products_cache,rebuild)
        return products_cache['data']
    def status_cached(ttl=60.0):
        now=time.time()
        if allow_cache.get('status') is None:
            allow_cache['status']=snapshot(root)
        elif now-allow_cache['at']>60.0 and not allow_cache.get('status_refreshing'):
            allow_cache['status_refreshing']=True
            def rebuild():
                try:allow_cache['status']=snapshot(root)
                finally:allow_cache['status_refreshing']=False
            threading.Thread(target=rebuild,daemon=True).start()
        return allow_cache['status']
    def media_allowed_cached(ttl=60.0):
        """Allowlist of /media/ URLs; rebuilding it per request re-runs four
        full catalogs and stalls every video byte under concurrent viewers."""
        now=time.time()
        if allow_cache['urls']:
            if now-allow_cache['at']>ttl and not allow_cache['refreshing']:
                allow_cache['refreshing']=True
                def rebuild():
                    try:allow_cache['urls']=_media_allowed()
                    finally:allow_cache['refreshing']=False
                threading.Thread(target=rebuild,daemon=True).start()
            return allow_cache['urls']
        allow_cache['urls']=_media_allowed();allow_cache['at']=time.time()
        return allow_cache['urls']
    def _media_allowed():
        allowed=set()
        from .pipeline_status import snapshot as pipeline_snapshot
        pipeline=pipeline_snapshot(root)
        allowed.update(row['url'] for row in pipeline['keyframes'])
        if pipeline['preview_url']:allowed.add(pipeline['preview_url'])
        allowed.update(review_packet_catalog(root)['media_urls'])
        for item in style_catalog(root)['items']:
            allowed.update(item.get(k) for k in ('audio_url','video_url','cover_url','publication_url'))
        for job in snapshot(root)['jobs']:
            allowed.update(job.get(k) for k in ('video_url', 'cover_url', 'publication_url', 'quality_url'))
            for task in job['tasks']:
                allowed.update(task.get(k) for k in ('video_url', 'audio_url'))
        return allowed
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_bytes(self, data, content_type, status=200):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            from .dashboard_actions import export_film,request_repair,ActionError
            host=self.headers.get('Host','')
            origin=self.headers.get('Origin')
            path=urlsplit(self.path).path
            view_hosts=[h.strip() for h in os.environ.get('WORKFLOW_DASHBOARD_VIEW_HOSTS', '').split(',') if h.strip()]
            review_token=os.environ.get('WORKFLOW_DASHBOARD_REVIEW_TOKEN','')
            local_ok=(host in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
                    and origin in (None,'http://'+host)
                    and self.headers.get('X-Workflow-Token')==action_token)
            # Delegated reviewers may only approve or file repairs through the
            # exposed view host with their own token; export stays local-only.
            reviewer_ok=(bool(review_token)
                    and host.split(':')[0] in view_hosts
                    and origin in (None,'https://'+host,'http://'+host)  # Tailscale peers ride plain http
                    and self.headers.get('X-Workflow-Token')==review_token
                    and path in ('/api/approve-product','/api/request-repair','/api/archive-product','/api/add-material'))
            if not (local_ok or reviewer_ok):
                self.send_error(403);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=20000 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ActionError('请求格式无效。')
                body=json.loads(self.rfile.read(size));path=urlsplit(self.path).path
                if path=='/api/export-folder':
                    result=export_film(root,body.get('job_id'),body.get('video_sha256'),body.get('folder'))
                elif path=='/api/approve-product':
                    from .product_approval import approve
                    result=approve(root,body.get('job_id'),body.get('video_sha256'))
                elif path=='/api/request-repair':
                    result=request_repair(root,body.get('job_id'),body.get('video_sha256'),body.get('note'),body.get('time_seconds'),body.get('request_id'))
                elif path=='/api/archive-product':
                    from .board import publish
                    result=publish(root,body.get('product_id'))
                elif path=='/api/add-material':
                    from .material_submission import submit
                    result=submit(root,body.get('text'),body.get('title'),body.get('url'))
                else:self.send_error(404);return
                self.send_bytes(json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            except (ValueError,TypeError) as e:
                self.send_bytes(json.dumps({'error':str(e)},ensure_ascii=False).encode(),'application/json; charset=utf-8',400)
            except OSError:
                self.send_bytes(json.dumps({'error':'无法写入该文件夹，请检查路径和权限。'},ensure_ascii=False).encode(),'application/json; charset=utf-8',400)

        def do_GET(self):
            host = self.headers.get('Host', '').split(':')[0]
            # Read-only viewers may be exposed through an explicit tunnel host;
            # actions (POST) stay localhost+token regardless of this allowlist.
            extra = [h.strip() for h in os.environ.get('WORKFLOW_DASHBOARD_VIEW_HOSTS', '').split(',') if h.strip()]
            if host not in ('127.0.0.1', 'localhost', *extra):
                self.send_error(403); return
            path = urlsplit(self.path).path
            if path=='/actions.js':
                self.send_bytes(Path(__file__).with_name('dashboard_actions.js').read_bytes(),'text/javascript; charset=utf-8');return
            if path=='/api/pipeline':
                from .pipeline_status import snapshot as pipeline_snapshot
                self.send_bytes(json.dumps(pipeline_snapshot(root),ensure_ascii=False).encode(),'application/json; charset=utf-8');return
            if path=='/pipeline':
                self.send_bytes(Path(__file__).with_name('pipeline.html').read_bytes(),'text/html; charset=utf-8');return
            if path=='/board':
                self.send_bytes(Path(__file__).with_name('board.html').read_bytes(),'text/html; charset=utf-8');return
            if path=='/api/board':
                from .board import snapshot as board_snapshot
                self.send_bytes(json.dumps(board_cached(),ensure_ascii=False).encode(),'application/json; charset=utf-8');return
            if path=='/api/delivery-actions':
                from .dashboard_actions import preferences,repair_requests
                # Include the reviewer token so remote Tailscale peers can operate;
                # GET access itself is already gated by the view-host allowlist.
                reviewer_token=os.environ.get('WORKFLOW_DASHBOARD_REVIEW_TOKEN','')
                data={'items':catalog_cached(),'preferences':preferences(root),'repair_requests':repair_requests(root),'token':action_token,'review_token':reviewer_token}
                self.send_bytes(json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8');return
            if path == '/api/products':
                data=products_cached()
                with sqlite3.connect(root/'queue.sqlite') as c:
                    exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='editorial_backlog'").fetchone()
                    backlog=dict(c.execute('SELECT state,count(*) FROM editorial_backlog GROUP BY state').fetchall()) if exists else {}
                self.send_bytes(json.dumps({'products':data,'editorial_backlog':backlog},ensure_ascii=False).encode(),'application/json; charset=utf-8');return
            if path == '/api/styles':
                self.send_bytes(json.dumps(style_catalog(root),ensure_ascii=False).encode(),'application/json; charset=utf-8');return
            if path in ('/review-packets','/speech-review','/motion-review','/acceptance'):
                page=review_packet_catalog(root).get({'/review-packets':'index_path','/speech-review':'speech_index_path','/motion-review':'motion_index_path','/acceptance':'acceptance_index_path'}[path])
                if not page: self.send_error(404); return
                self.send_bytes(Path(page).read_bytes(),'text/html; charset=utf-8');return
            if path == '/styles':
                self.send_bytes(Path(__file__).with_name('styles.html').read_bytes(),'text/html; charset=utf-8');return
            if path == '/api/status':
                try:
                    self.send_bytes(json.dumps(status_cached(), ensure_ascii=False).encode(), 'application/json; charset=utf-8')
                except Exception:
                    self.send_bytes(b'{"error":"status temporarily unavailable"}', 'application/json', 503)
                return
            if path == '/tasks':
                self.send_bytes(Path(__file__).with_name('dashboard.html').read_bytes(), 'text/html; charset=utf-8'); return
            if path in ('/', '/index.html'):
                self.send_bytes(Path(__file__).with_name('board.html').read_bytes(), 'text/html; charset=utf-8'); return
            if path == '/products':
                self.send_bytes(Path(__file__).with_name('products.html').read_bytes(), 'text/html; charset=utf-8'); return
            if path.startswith('/product-media/'):
                item=next((x for x in catalog_cached() if x['id']==unquote(path[len('/product-media/'):])),None)
                if not item:self.send_error(404);return
                target=Path(item['video'])
            else:
                if not path.startswith('/media/'):
                    self.send_error(404); return
                target = (root / unquote(path[len('/media/'):])).resolve()
                if not target.is_relative_to(root) or not target.is_file() or target.suffix.lower() not in ('.mp4', '.png', '.jpg', '.wav', '.mp3', '.txt', '.json'):
                    self.send_error(404); return
                # No arbitrary config files: media paths must be among recorded deliverables.
                allowed = media_allowed_cached()
                if '/media/' + quote(str(target.relative_to(root)), safe='/') not in allowed:
                    self.send_error(404); return
            size = target.stat().st_size
            start, end, status = 0, size - 1, 200
            range_header = self.headers.get('Range')
            if range_header:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
                if not match or not any(match.groups()):
                    self.send_error(416); return
                left, right = match.groups()
                if left:
                    start = int(left); end = min(int(right), size - 1) if right else size - 1
                else:
                    start = max(0, size - int(right))
                if start >= size or end < start:
                    self.send_response(416); self.send_header('Content-Range', f'bytes */{size}'); self.end_headers(); return
                status = 206
            self.send_response(status)
            self.send_header('Content-Type', mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end - start + 1))
            if status == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if self.command != 'HEAD':
                with target.open('rb') as handle:
                    handle.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = handle.read(min(1024 * 256, remaining))
                        if not chunk: break
                        try: self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError): break
                        remaining -= len(chunk)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='output/production')
    parser.add_argument('--port', type=int, default=8768)
    # Default stays loopback-only; remote hosts (e.g. the Windows production
    # box reached over Tailscale) pass --host 0.0.0.0 explicitly.
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.root))
    print(f'Production monitor: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
