"""Exact caption intervals shared by the renderer and sampled-frame reviewer."""
import re

def events(plan):
    policy = plan.get('caption_policy', 'legacy_character_v1')
    if policy not in ('legacy_character_v1', 'speech_aligned_v1'):
        raise ValueError('unknown caption_policy')
    if policy == 'speech_aligned_v1':
        import json
        from pathlib import Path
        from .speech_timing import aligned_events
        result = []
        for i, n in enumerate(plan['narration']):
            if not n.get('word_timing_path'):
                raise ValueError('speech_aligned_v1 requires word_timing_path for every narration')
            timing = json.loads(Path(n['word_timing_path']).read_text(encoding="utf-8"))
            result.extend(aligned_events(n, timing, i))
        return result
    result=[]
    for i,n in enumerate(plan['narration']):
        text=re.sub(r'(?<=[A-Z]) +(?=[A-Z])','',n.get('caption_text',n['text']))
        phrases=re.findall(r'[^，。！？；、]+[，。！？；、]?',text)
        chunks=[part[j:j+11] for part in phrases for j in range(0,len(part),11)]
        cursor=n['start']
        for chunk in chunks:
            end=cursor+(n['end']-n['start'])*len(chunk)/len(text)
            result.append({'narration_index':i,'start':cursor,'end':end,'text':chunk});cursor=end
    return result


def review_evidence(narration):
    """Distinguish a narration slot (including padding) from estimated speech.

    This reports existing hash-bound cues; it never edits audio/captions or
    labels ASR estimates as a listening verification.
    """
    import json
    from pathlib import Path
    from .core.io import sha256
    from .speech_timing import aligned_events
    rows=[]
    for index,line in enumerate(narration):
        row={'narration_index':index,'slot_start':line['start'],'slot_end':line['end'],
             'slot_end_is_not_spoken_content_end':True,'listening_verified':False}
        path=line.get('word_timing_path')
        if not path:
            row.update(status='unavailable',reason='No word cues; do not infer ongoing speech from slot end')
        else:
            timing=json.loads(Path(path).read_text(encoding="utf-8"))
            captions=aligned_events(line,timing,index)
            last=max(event['end'] for event in captions)
            row.update(status='hash_bound_word_cues',word_timing_path=path,
                       word_timing_sha256=sha256(path),source_sha256=timing['source_sha256'],
                       method=timing['method'],estimated_last_word_end=last,
                       trailing_slot_seconds=round(line['end']-last,6),
                       caption_intervals=[{k:event[k] for k in ('start','end','text')} for event in captions],
                       limits='Word cues are timing evidence, not proof of silence or a new audio listening review')
        rows.append(row)
    return rows
