"""Local full-film research viewer. Source videos and evidence only; no general file access."""
import argparse
import json
import re
import mimetypes
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import unquote,urlsplit

def snapshot(root):
    root=Path(root);videos=[]
    for folder in sorted(root.glob('BV*')):
        if not (folder/'evidence.json').exists():continue
        info=json.loads((folder/'evidence.json').read_text(encoding="utf-8"));shots=[];batch_count=0
        for path in sorted((folder/'full/reviews').glob('*.json')):
            batch=json.loads(path.read_text(encoding="utf-8"));batch_count+=1
            for row in batch['analysis']['shots']:
                shots.append({**row,'batch_id':batch['batch_id'],'evidence_urls':[f'/asset/{folder.name}/full/sheets/{Path(p).name}' for p in batch.get('evidence_sheets',[])]})
        for i,row in enumerate(shots):row['id']=f'S{i+1:04d}'
        summary=folder/'full/synthesis.json'
        total=folder/'full/batches.json'
        videos.append({'id':folder.name,'creator':info['creator'],'title':info['title'],'source_url':info['source_url'],
                       'duration_seconds':info['duration_seconds'],'reviewed_seconds':sum(s['end']-s['start'] for s in shots),
                       'reviewed_batches':batch_count,'total_batches':len(json.loads(total.read_text(encoding="utf-8"))['batches']) if total.exists() else None,
                       'shots':shots,'summary':json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else None,
                       'downloads':{ext:f'/asset/{folder.name}/full/shot_table.{ext}' for ext in ('csv','json') if (folder/f'full/shot_table.{ext}').exists()},
                       'video_url':f'/asset/{folder.name}/'+('source_verified.mp4' if (folder/'source_verified.mp4').exists() else 'source.mp4')})
    p=root/'full_progress.json'
    return {'progress':json.loads(p.read_text(encoding="utf-8")) if p.exists() else {},'videos':videos}

def handler(root):
    root=Path(root).resolve()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send_data(self,data,mime):
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers()
            if self.command!='HEAD':self.wfile.write(data)
        def do_HEAD(self):self.do_GET()
        def do_GET(self):
            if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost'):self.send_error(403);return
            path=unquote(urlsplit(self.path).path)
            if path=='/api/study':self.send_data(json.dumps(snapshot(root),ensure_ascii=False).encode(),'application/json; charset=utf-8');return
            if path=='/':self.send_data(Path(__file__).with_name('reference_dashboard.html').read_bytes(),'text/html; charset=utf-8');return
            if not re.fullmatch(r'/asset/BV[a-zA-Z0-9]+/(source(?:_verified)?\.mp4|full/sheets/batch_\d+_\d+\.jpg|full/shot_table\.(csv|json))',path):self.send_error(404);return
            file=(root/path[7:]).resolve()
            if not file.is_relative_to(root) or not file.is_file():self.send_error(404);return
            size=file.stat().st_size;start,end,status=0,size-1,200
            value=self.headers.get('Range')
            if value:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
                if not match or not any(match.groups()):self.send_error(416);return
                a,b=match.groups()
                if a:start=int(a);end=min(int(b),size-1) if b else size-1
                else:start=max(0,size-int(b))
                if start>=size or end<start:self.send_error(416);return
                status=206
            self.send_response(status);self.send_header('Content-Type',mimetypes.guess_type(str(file))[0]);self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes')
            if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            if self.command=='HEAD':return
            with file.open('rb') as f:
                f.seek(start);left=end-start+1
                while left:
                    b=f.read(min(left,262144))
                    if not b:break
                    try:self.wfile.write(b)
                    except (BrokenPipeError,ConnectionResetError):break
                    left-=len(b)
    return Handler

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='research/reference_study');parser.add_argument('--port',type=int,default=8769);args=parser.parse_args()
    ThreadingHTTPServer(('127.0.0.1',args.port),handler(args.root)).serve_forever()
