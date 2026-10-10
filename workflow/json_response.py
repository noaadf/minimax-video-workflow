"""Strict JSON with one lossless concession: bare identifier object keys."""
import json,re

def _pairs(items):
    d={}
    for key,value in items:
        if key in d:raise ValueError('duplicate JSON key')
        d[key]=value
    return d

def parse_object(text):
    text=text.strip()
    if text.startswith('```'):
        match=re.fullmatch(r'```(?:json)?\s*\n([\s\S]*?)\n?```',text)
        if not match:raise ValueError('invalid JSON fence')
        text=match.group(1).strip()
    def load(s):
        return json.loads(s,object_pairs_hook=_pairs,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('nonfinite JSON')))
    normalized=False
    try:value=load(text)
    except json.JSONDecodeError:
        # Tokenize strings verbatim. Only quote an identifier after { or , followed by :.
        pieces=[];i=0;after_separator=False
        while i<len(text):
            ch=text[i]
            if ch=='"':
                start=i;i+=1
                while i<len(text):
                    if text[i]=='\\':i+=2;continue
                    if text[i]=='"':i+=1;break
                    i+=1
                pieces.append(text[start:i]);after_separator=False;continue
            if after_separator and not ch.isspace():
                m=re.match(r'([A-Za-z_][A-Za-z0-9_]*)(\s*):',text[i:])
                if m:
                    pieces.append(json.dumps(m.group(1))+m.group(2)+':');i+=len(m.group(0));after_separator=False;normalized=True;continue
                after_separator=False
            pieces.append(ch);i+=1
            if ch in '{,':after_separator=True
        if not normalized:raise
        value=load(''.join(pieces))
    if not isinstance(value,dict):raise ValueError('JSON response must be an object')
    return value,normalized


def parse_review_with_summary(text):
    """Recover only a complete review object followed by a labelled prose summary.

    Never completes truncated JSON, changes a verdict, or accepts a second object.
    The caller must preserve the raw response and record this normalization.
    """
    decoder = json.JSONDecoder(object_pairs_hook=_pairs,
        parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    value, end = decoder.raw_decode(text.strip())
    suffix = text.strip()[end:].strip()
    if not suffix.startswith('**Assessment summary:**') or any(c in suffix for c in '{}[]'):
        raise ValueError('unrecognized or ambiguous review suffix')
    if not isinstance(value, dict) or value.get('verdict') not in ('pass','review','reject'):
        raise ValueError('missing review verdict')
    confidence = value.get('confidence')
    if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
        raise ValueError('invalid review confidence')
    if not isinstance(value.get('findings'), list) or not isinstance(value.get('not_assessed'), list):
        raise ValueError('incomplete review schema')
    for finding in value['findings']:
        if (not isinstance(finding,dict) or finding.get('severity') not in ('low','medium','high')
            or not isinstance(finding.get('issue'),str) or not isinstance(finding.get('shot_ids'),list)):
            raise ValueError('invalid finding')
    return value
