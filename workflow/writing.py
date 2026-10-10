"""Bounded script revisions driven by measured speech, preserving the factual input."""
import json
import re
from .llm import ask_json


def shorten(plan, oversized):
    entries=[]
    for item in oversized:
        i=item['index'];n=plan['narration'][i]
        budget=max(3,int(len(n['text'])*(item['slot_seconds']-.12)/item['source_seconds']*.90))
        entries.append({'index':i,'text':n['text'],'max_characters':budget,'fact_ids':n.get('fact_ids',[])})
    prompt='''Shorten ONLY the listed Mandarin narration segments to fit their measured voice duration.
Keep the central factual meaning and existing narrative arc. Preserve creative metaphors, subjective opinions and emotional phrasing; these do not require literal source wording. You may omit lower-priority detail. Do not add claims,
figures, dates, records, inferred outcomes or accidents. Prefer expressive natural short sentences to labels.
Retain the opening question as a question; preserve cause/effect and closing callback. Never reduce a sentence to disconnected noun labels.
Follow the supplied audiovisual profile and generic voice, not a fixed documentary male voice.
The character budget is a HARD maximum, counting punctuation. Preserve factual numerical values if retained.
Return JSON ONLY: {"revisions":[{"index":int,"text":string}]} with each requested index exactly once.
No timing or source changes.\nFacts: '''+json.dumps(plan['facts'],ensure_ascii=False)+'\nSegments: '+json.dumps(entries,ensure_ascii=False)
    feedback=None
    budgets={e['index']:e['max_characters'] for e in entries}
    for attempt in range(3):
        result,receipt=ask_json(prompt,{'facts':plan['facts'],'segments':entries,'feedback':feedback,'voice':plan.get('voice'),'profile':plan.get('audiovisual_profile')},max_tokens=16000)
        revisions=result.get('revisions') if isinstance(result,dict) else None
        normalized=[]
        if isinstance(revisions,list):
            for row in revisions:
                if not isinstance(row,dict):break
                index=row.get('index')
                if isinstance(index,str) and re.fullmatch(r'0|[1-9][0-9]*',index):index=int(index)
                if type(index) is not int:break
                normalized.append({**row,'index':index})
        if (not isinstance(revisions,list) or len(normalized)!=len(revisions)
                or len(normalized)!=len(budgets) or {x['index'] for x in normalized}!=set(budgets)):
            feedback={'error':'narration repair changed segment identities','required_indices':list(budgets),
                      'prior_revisions':revisions,
                      'instruction':'Return the COMPLETE revisions list with every requested index exactly once, including lines already within budget. Never return only the previously invalid lines.'}
            continue
        revisions=normalized
        invalid=[{'index':x['index'],'actual_characters':len(x['text']) if isinstance(x.get('text'),str) else None,'maximum':budgets[x['index']]} for x in revisions
                 if not isinstance(x.get('text'),str) or not x['text'].strip() or len(x['text'])>budgets[x['index']]]
        if not invalid:
            return revisions, receipt
        feedback={'invalid':invalid,'prior_revisions':revisions,'required_indices':list(budgets),'instruction':'Count punctuation; aim at least two characters under each limit. Return the COMPLETE list of all requested indices, not only invalid lines.'}
    raise ValueError('narration repair failed segment identities or measured character budget after three validation attempts: '+str(feedback))
