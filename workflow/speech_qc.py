"""Deterministic comparison of unprompted ASR against narration, never pronunciation approval."""
import difflib
import re
import unicodedata
from opencc import OpenCC

_SIMPLIFY=OpenCC('t2s')

DIGITS=dict(zip('零〇一二两三四五六七八九','001223456789'))
UNITS={'十':10,'百':100,'千':1000,'万':10000,'亿':100000000}
NUMBER=re.compile(r'[零〇一二两三四五六七八九十百千万亿0-9]+(?:[点.][零〇一二两三四五六七八九0-9]+)?')


def integer(text):
    if not any(c in UNITS for c in text):
        return str(int(''.join(DIGITS.get(c,c) for c in text)))
    total=section=number=0
    for c in text:
        if c in UNITS:
            unit=UNITS[c]
            if unit<10000:
                section+=(number or 1)*unit;number=0
            else:
                section+=number
                total=(total+section or 1)*unit if unit==100000000 else total+(section or 1)*unit
                section=number=0
        else:
            number=number*10+int(DIGITS.get(c,c))
    return str(total+section+number)


def pieces(text):
    text=_SIMPLIFY.convert(unicodedata.normalize('NFKC',text)).lower()
    result=[];cursor=0
    def literal(value):
        result.extend({'normalized':c,'original':c} for c in value if c.isalnum())
    for match in NUMBER.finditer(text):
        literal(text[cursor:match.start()])
        parts=re.split('[点.]',match[0])
        value=integer(parts[0])
        normalized=value+('.'+''.join(DIGITS.get(c,c) for c in parts[1]) if len(parts)>1 else '')
        result.append({'normalized':normalized,'original':match[0]});cursor=match.end()
    literal(text[cursor:])
    return result


def normalize(text):
    return ''.join(p['normalized'] for p in pieces(text))


def original_fragment(text, interval):
    cursor=0;found=[]
    for p in pieces(text):
        end=cursor+len(p['normalized'])
        if cursor<interval[1] and end>interval[0]:found.append(p['original'])
        cursor=end
    return ''.join(found)


def classify_phonetics(expected, recognized):
    from pypinyin import lazy_pinyin, pinyin, Style
    result=compare(expected,recognized)
    for d in result['differences']:
        a=original_fragment(expected,d['expected_range']);b=original_fragment(recognized,d['recognized_range'])
        pa=lazy_pinyin(a,style=Style.TONE3,neutral_tone_with_five=True)
        pb=lazy_pinyin(b,style=Style.TONE3,neutral_tone_with_five=True)
        classification = 'homophone_spelling' if pa == pb else 'phonetic_difference'
        if pa != pb and a and len(a) == len(b):
            # Alternate dictionary readings are uncertainty, never proof of what was heard.
            options = dict(style=Style.TONE3, neutral_tone_with_five=True, heteronym=True)
            alternatives_a = [pinyin(c, **options)[0] for c in a]
            alternatives_b = [pinyin(c, **options)[0] for c in b]
            if all(set(x) & set(y) for x, y in zip(alternatives_a, alternatives_b)):
                classification = 'polyphone_ambiguity'
                d.update(expected_reading_candidates=alternatives_a,
                         recognized_reading_candidates=alternatives_b)
        d.update(expected_written=a,recognized_written=b,expected_pinyin=pa,recognized_pinyin=pb,
                 classification=classification)
    result['phonetic_caveat']='Dictionary pinyin only; does not hear or certify actual tones. Homophone spelling differences do not prove a voice error.'
    return result


def compare(expected, observed):
    a=normalize(expected);b=normalize(observed)
    changes=[{'operation':op,'expected':a[i:j],'recognized':b[k:l],
              'expected_range':[i,j],'recognized_range':[k,l]}
             for op,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes() if op!='equal']
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current=[i]
        for j,y in enumerate(b,1):
            current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
        previous=current
    distance=previous[-1]
    return {'expected_normalized':a,'recognized_normalized':b,'edit_distance':distance,
            'character_error_rate':round(distance/max(1,len(a)),4),'differences':changes,
            'status':'transcript_match' if not changes else 'transcript_differences_require_review',
            'content_approved':False,
            'limits':['ASR can misrecognize technical terms and homophones.',
                      'Matching text is not a pronunciation, prosody or timbre certification.',
                      'Only traditional/simplified writing, Unicode/case/punctuation and numerical notation normalized; no homophone substitutions.']}
