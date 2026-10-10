"""Bounded public HTTPS download with verifiable, restartable range chunks."""
import concurrent.futures
import re
import urllib.request
from pathlib import Path
from .core import canonical_url, sha256, write_json


def download(url, destination, max_bytes=150*1024*1024, chunk_bytes=1024*1024):
    url = canonical_url(url)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=25) as r:
        canonical_url(r.url)
        total = int(r.headers['Content-Length'])
        etag = r.headers.get('ETag')
    if not 0 < total <= max_bytes:
        raise ValueError('media exceeds download bound')
    chunks = destination.with_name(destination.name + '.parts')
    chunks.mkdir(exist_ok=True)
    identity = {'url': url, 'bytes': total, 'etag': etag, 'chunk_bytes': chunk_bytes}
    manifest = chunks/'download.json'
    if manifest.exists():
        import json
        if json.loads(manifest.read_text(encoding="utf-8")) != identity:
            raise ValueError('remote object changed; use new archive path')
    else:
        write_json(manifest, identity)

    def get(start):
        end = min(total, start+chunk_bytes)-1
        path = chunks/f'{start:012d}.part'
        if path.exists() and path.stat().st_size == end-start+1:
            return path
        headers = {'Range': f'bytes={start}-{end}'}
        if etag: headers['If-Range'] = etag
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                    canonical_url(r.url)
                    if r.status != 206 or r.headers.get('Content-Range') != f'bytes {start}-{end}/{total}':
                        raise ValueError('server did not honor exact byte range')
                    if etag and r.headers.get('ETag') != etag:
                        raise ValueError('media ETag changed')
                    blob = r.read(end-start+2)
                if len(blob) != end-start+1:
                    raise ValueError('partial range response')
                temp = path.with_suffix('.tmp'); temp.write_bytes(blob); temp.replace(path)
                return path
            except (OSError, ValueError):
                if attempt == 2: raise
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        parts = list(pool.map(get, range(0, total, chunk_bytes)))
    temp = destination.with_name(destination.name+'.complete.tmp')
    with temp.open('wb') as f:
        for part in parts: f.write(part.read_bytes())
    if temp.stat().st_size != total:
        raise ValueError('assembled media size mismatch')
    if destination.exists() and sha256(destination) != sha256(temp):
        raise ValueError('destination already contains other bytes')
    temp.replace(destination)
    receipt = {**identity, 'sha256': sha256(destination), 'status': 'archived_complete',
               'verification': 'exact HTTPS ranges, stable ETag, complete byte count, local SHA256'}
    write_json(destination.with_suffix('.download.json'), receipt)
    return receipt


if __name__ == '__main__':
    import argparse,json
    p=argparse.ArgumentParser();p.add_argument('url');p.add_argument('destination')
    a=p.parse_args();print(json.dumps(download(a.url,a.destination)))
