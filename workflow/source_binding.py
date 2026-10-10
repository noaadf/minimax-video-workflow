"""Bind fact provenance to archived metadata without inventing missing values."""
import copy,json
from pathlib import Path
from .core.io import sha256
from .ingest import parse
from .fact_gate import requires_archive_binding

def bind(facts,packet):
 archive=Path(packet['archive']);source=json.loads(archive.read_text(encoding="utf-8"));html=archive.with_name('source.html')
 if sha256(html)!=source['content_sha256']:raise ValueError('Source archive changed')
 if packet['source_url']!=source['url']:raise ValueError('Source URL differs')
 text=parse(html.read_text(encoding='utf-8'),source['url'])['text'];result=copy.deepcopy(facts)
 for f in result:
  if not requires_archive_binding(f):
   # User facts are accepted directly (owner directive 2026-10-05) and never
   # rewritten with archive provenance; they stay visibly user-sourced in the manifest.
   # Policy lives in fact_gate — single source of truth for provenance gating.
   continue
  quote=f.get('evidence_quote','')
  if len(quote.strip())<12 or quote not in source['text'] or quote not in text:raise ValueError('Fact quote not in archived source')
  if f.get('source_url',source['url'])!=source['url'] or f.get('source_sha256',source['content_sha256'])!=source['content_sha256']:raise ValueError('Fact source mismatch')
  f.update(source_url=source['url'],source_sha256=source['content_sha256'],source_published=source.get('published'))
 return result
