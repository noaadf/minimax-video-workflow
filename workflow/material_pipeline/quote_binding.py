"""Bind model quote whitespace to source bytes without changing a verdict.

The exact-quote gate remains unchanged. Only whitespace runs may differ; words,
punctuation and source identity must match, and the recovered quote must be
unambiguous. Callers retain the untouched model response and binding audit.
"""
import copy
import re
from .story_gate import ROLES, source_documents

def bind_whitespace_quotes(answer, documents):
    bound=copy.deepcopy(answer);changes=[]
    for role in ROLES:
        for evidence in bound.get('source_story',{}).get(role,{}).get('evidence',[]):
            source_id=evidence.get('source_id');quote=evidence.get('quote')
            text=documents.get(source_id)
            if not isinstance(quote,str) or not isinstance(text,str) or len(quote.strip())<8 or quote in text:
                continue
            # Preserve whitespace boundaries: no joined/split-word matching,
            # Unicode punctuation folding, paraphrasing or missing text repair.
            parts=re.split(r'(\s+)',quote)
            pattern=''.join(r'\s+' if p and p.isspace() else re.escape(p) for p in parts)
            matches={m.group(0) for m in re.finditer(pattern,text)}
            if len(matches)!=1:
                continue
            exact=matches.pop()
            evidence['quote']=exact
            changes.append(dict(role=role,source_id=source_id,original_quote=quote,exact_source_quote=exact,change='whitespace_only'))
    return bound,changes

def bind_source_quotes(package, root, answer):
    documents={s['id']:s['text'] for s in source_documents(package,root)}
    return bind_whitespace_quotes(answer,documents)
