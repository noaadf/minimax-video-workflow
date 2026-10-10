"""User-submitted material intake: raw article/script text enters the library.

Submissions are the *entry* of the material pipeline, not production requests:
the editorial agent (material_pipeline) turns an approved submission into a
package and only then does it become a production_request. States:
pending_editorial_review -> (agent) accepted/rejected -> production flow.
"""
import time
from pathlib import Path
from urllib.parse import urlparse

from .core.io import digest, sha256, write_json

MAX_CHARS = 200_000
MIN_CHARS = 50


def _validate(text, url):
    if not text and not url:
        raise ValueError('需要提供文章/剧本文本或来源链接之一。')
    if text and len(text) > MAX_CHARS:
        raise ValueError(f'文本过长（>{MAX_CHARS} 字符），请拆分提交。')
    if text and len(text.strip()) < MIN_CHARS:
        raise ValueError('文本太短，不足以支撑一个故事。')
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('来源链接必须是合法的 http(s) URL。')


def submit(root, text, title, url):
    """Persist one submission; idempotent for identical content."""
    text = (text or '').strip() or None
    title = (title or '').strip()
    _validate(text, url)
    root = Path(root)
    content_id = 'submission_' + digest([text, url])[:12]
    folder = root / 'material_intake' / 'submissions' / content_id
    marker = folder / 'submission.json'
    if marker.exists():
        return {'content_id': content_id, 'state': 'pending_editorial_review', 'duplicate': True}
    folder.mkdir(parents=True, exist_ok=True)
    if text:
        source = folder / 'source.txt'
        source.write_text(text, encoding='utf-8')
    record = {'content_id': content_id, 'title': title or (url or '')[:60],
              'url': url, 'has_text': bool(text),
              'source_sha256': sha256(folder / 'source.txt') if text else None,
              'chars': len(text or ''), 'state': 'pending_editorial_review',
              'received': time.time()}
    write_json(marker, record)
    return {'content_id': content_id, 'state': record['state'], 'title': record['title']}


def submissions(root):
    """All submissions, newest first, for the board's material lane."""
    root = Path(root)
    out = []
    for marker in (root / 'material_intake' / 'submissions').glob('*/submission.json'):
        try:
            record = json_loads_safe(marker)
        except (OSError, ValueError):
            continue
        out.append(record)
    out.sort(key=lambda r: r.get('received', 0), reverse=True)
    return out


def json_loads_safe(path):
    import json
    return json.loads(Path(path).read_text(encoding='utf-8'))
