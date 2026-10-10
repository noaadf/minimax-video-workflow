"""Bind a publisher's dated article card without altering the article body."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


class Card(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current = [dict(attrs).get('href', ''), '']

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.anchors.append(tuple(self.current))
            self.current = None


class ArticleSpans(HTMLParser):
    """Locate actual, unnested article elements, excluding inert HTML text."""
    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.raw = raw
        self.lines = [0]
        self.lines.extend(m.end() for m in re.finditer('\n', raw))
        self.inert = []
        self.starts = []
        self.spans = []

    def source_position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'template'):
            self.inert.append(tag)
        if self.inert:
            return
        if tag == 'article':
            self.starts.append(self.source_position())

    def handle_endtag(self, tag):
        if self.inert:
            if tag == self.inert[-1]:
                self.inert.pop()
            return
        if tag == 'article' and self.starts:
            start = self.starts.pop()
            if not self.starts:
                end = self.raw.index('>', self.source_position()) + 1
                self.spans.append((start, end))


def identity(url):
    from .core import canonical_url
    p = urlsplit(canonical_url(url))
    return p.hostname.removeprefix('www.'), p.path, p.query


def validate(source, root):
    from .core import local_path, sha256, _validate_publication_date
    from workflow.ingest import parse
    proof = source['publication_index']
    if proof.get('kind') != 'publisher_article_card':
        raise ValueError('Unknown publication index evidence kind')
    if identity(source['url'])[0] != identity(proof['url'])[0]:
        raise ValueError('Publication index must belong to the original publisher')
    for field, hashfield in [('archive_path', 'sha256'), ('text_path', 'text_sha256')]:
        if sha256(local_path(root, proof[field])) != proof[hashfield]:
            raise ValueError('Publication index hash changed')
    raw = local_path(root, proof['archive_path']).read_text(encoding='utf-8')
    body = local_path(root, proof['text_path']).read_text(encoding="utf-8")
    if parse(raw, proof['url'])['text'] != body:
        raise ValueError('Publication index text differs from original HTML')
    start, end = proof['entry_start'], proof['entry_end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw):
        raise ValueError('Publication index entry span invalid')
    entry = raw[start:end]
    spans = ArticleSpans(raw); spans.feed(raw)
    if (start, end) not in spans.spans:
        raise ValueError('Publication index entry is not an original article element')
    if (not re.fullmatch(r'<article\b[^>]*>.*</article>', entry, re.S)
            or len(re.findall(r'<article\b', entry)) != 1
            or len(re.findall(r'</article\s*>', entry)) != 1):
        raise ValueError('Publication date must bind exactly one article card')
    parser = Card(); parser.feed(entry)
    matching = [title.strip() for href, title in parser.anchors
                if href and identity(urljoin(proof['url'], href)) == identity(source['url'])]
    if matching != [source['title']]:
        raise ValueError('Publication index title/link does not identify the article')
    card_body = parse(entry, proof['url'])['text']
    _validate_publication_date(source, card_body)
    if source['date_evidence'] not in body or source['title'] not in source['date_evidence']:
        raise ValueError('Publication index date evidence must include the linked title')
    return {'url': proof['url'], 'published': source['published'],
            'date_evidence': source['date_evidence'], 'text_sha256': proof['text_sha256'],
            'entry_start': start, 'entry_end': end,
            'scope': 'Date belongs to the same publisher article card; original article body is unchanged'}
