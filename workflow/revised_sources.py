"""Verify repair facts without replacing the original product's source identity.

Supplemental records are immutable archival evidence, not replacement product
sources. The explicit v1 record binds HTML bytes, a full reconstructed text,
publication date and original article URL. No network or model calls occur here.
"""
import datetime
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from .ingest import allowed_url, parse
from .fact_gate import requires_archive_binding
from .core.io import sha256

SUPPLEMENTAL_DOMAINS = ('esa.int', 'cas.cn', 'news.cn', 'xinhuanet.com')


def parsed_publication(raw, url, parsed):
    if parsed.get('published'):
        return parsed['published']
    # ESA multimedia pages put their release date on its own line immediately
    # after the title. Do not infer publication from an event date in body prose.
    if urlsplit(url).hostname in ('esa.int', 'www.esa.int'):
        lines = parsed['text'].splitlines()[:8]
        dates = [line for line in lines if re.fullmatch(r'\d{2}/\d{2}/20\d{2}', line)]
        if len(dates) == 1:
            return datetime.datetime.strptime(dates[0], '%d/%m/%Y').date().isoformat()
    raise ValueError('supplemental publication date unavailable from archived page')


def load_supplemental(entry, original_url):
    if not isinstance(entry, dict) or set(entry) != {'path', 'sha256'}:
        raise ValueError('supplemental source requires exact archive path/SHA binding')
    record_path = Path(entry['path']).resolve()
    if not record_path.is_file() or sha256(record_path) != entry['sha256']:
        raise ValueError('supplemental archive record hash mismatch')
    source = json.loads(record_path.read_text(encoding="utf-8"))
    if source.get('schema_version') != 1 or source.get('original_source_url') != original_url:
        raise ValueError('supplemental source belongs to another original article')
    url = source.get('url', '')
    if allowed_url(url, SUPPLEMENTAL_DOMAINS) != url:
        raise ValueError('supplemental URL must be canonical HTTPS without fragment')
    html = Path(source.get('html_path', '')).resolve()
    if not html.is_file() or sha256(html) != source.get('content_sha256'):
        raise ValueError('supplemental HTML hash mismatch')
    raw = html.read_text(encoding='utf-8')
    reconstructed = parse(raw, url)
    if source.get('text') != reconstructed['text'] or source.get('title') != reconstructed['title']:
        raise ValueError('supplemental parsed text/title differs from HTML')
    date = parsed_publication(raw, url, reconstructed)
    if source.get('published') != date:
        raise ValueError('supplemental publication date mismatch')
    return source, {'archive_path': str(record_path), 'archive_sha256': sha256(record_path),
                    'html_path': str(html), 'html_sha256': sha256(html), 'url': url,
                    'published': date}


def validate_revised_sources(plan, parent):
    packet = plan.get('source_packet', {})
    original = parent.get('source_packet', {})
    if not original or packet != original:
        raise ValueError('revised facts require unchanged original source identity')
    archive = Path(packet.get('archive', '')).resolve()
    source = json.loads(archive.read_text(encoding="utf-8"))
    html = archive.with_name('source.html')
    if sha256(html) != source.get('content_sha256'):
        raise ValueError('original source archive hash mismatch')
    reconstructed = parse(html.read_text(encoding='utf-8'), source['url'])
    if packet.get('source_url') != source['url']:
        raise ValueError('original source URL mismatch')
    entries = plan.get('supplemental_sources', [])
    if not isinstance(entries, list):
        raise ValueError('supplemental_sources must be an explicit list')
    sources = {source['url']: (source, reconstructed['text'])}
    receipts = []
    for entry in entries:
        extra, receipt = load_supplemental(entry, source['url'])
        if extra['url'] in sources:
            raise ValueError('duplicate supplemental source URL')
        sources[extra['url']] = (extra, extra['text'])
        receipts.append(receipt)
    ids = []
    for fact in plan['facts']:
        if not fact.get('id') or fact['id'] in ids:
            raise ValueError('fact IDs must be unique')
        ids.append(fact['id'])
        if not requires_archive_binding(fact):
            # Owner-authorized editorial fact (user channel): no archive binding.
            # Policy lives in fact_gate — single source of truth for provenance gating.
            continue
        bound = sources.get(fact.get('source_url'))
        if bound is None:
            raise ValueError('fact source is not archived and explicitly bound')
        evidence, reconstructed_text = bound
        if fact.get('source_sha256') != evidence['content_sha256']:
            raise ValueError('fact source URL/hash mismatch')
        quote = fact.get('evidence_quote')
        if not isinstance(quote, str) or len(quote.strip()) < 12 or quote not in evidence['text'] or quote not in reconstructed_text:
            raise ValueError('fact quote is not an exact archived source span')
        if fact.get('source_published') != evidence.get('published'):
            raise ValueError('fact source date mismatch')
    return {'source_archive': str(archive), 'source_html_sha256': source['content_sha256'],
            'source_url': source['url'], 'validated_fact_ids': ids,
            'supplemental_sources': receipts,
            'scope': 'exact archived evidence spans plus existing SHA-bound narrative source-support review'}
