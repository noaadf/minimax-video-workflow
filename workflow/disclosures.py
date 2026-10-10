"""One supplemental disclosure per timestamp; preserve distinct meanings or fail closed."""
import math


def normalize(rows, duration=30):
    # Quantize before comparison, matching ASS centisecond precision.
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('invalid disclosure duration')
    limit = round(duration * 100)
    clean = []
    for row in rows:
        start, end = float(row['start']), float(row['end'])
        text = row['text'].strip()
        if not all(math.isfinite(x) for x in (start, end)):
            raise ValueError('nonfinite disclosure interval')
        a, b = round(start * 100), round(end * 100)
        if not text or a < 0 or b > limit or a >= b:
            raise ValueError('invalid disclosure interval or text')
        clean.append((a, b, text))
    points = sorted({v for a, b, _ in clean for v in (a, b)})
    result = []
    for a, b in zip(points, points[1:]):
        texts = {text for start, end, text in clean if start <= a and end >= b}
        if len(texts) > 1:
            raise ValueError(f'conflicting disclosures at {a/100:g}..{b/100:g}; resolve meaning explicitly')
        if not texts:
            continue
        text = texts.pop()
        if result and result[-1]['text'] == text and result[-1]['end'] == a / 100:
            result[-1]['end'] = b / 100
        else:
            result.append({'start': a / 100, 'end': b / 100, 'text': text})
    return result
