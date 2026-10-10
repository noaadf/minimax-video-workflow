"""Bounded local timing repair; never changes speech, shots, or playback speed."""
import math


def extend_into_pause(plan, measured):
    if plan.get('pacing_policy') != 'narrative_timed_v1' or plan.get('locked_audio'):
        return []
    rows = plan['narration']
    if len(rows) != len(measured):
        raise ValueError('speech measurement count mismatch')
    boundaries = []
    total = 0
    for shot in plan['shots']:
        total += shot['duration_seconds']
        boundaries.append(total)
    changes = []
    for i, (row, seconds) in enumerate(zip(rows, measured)):
        if not row.get('preserve_delivery') or not row.get('audio_path'):
            continue
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('invalid speech measurement')
        start, end = row['start'], row['end']
        if seconds <= end - start - .08:
            continue
        required = round(start + seconds + .10, 6)
        shot_end = next((x for x in boundaries if x > start), start)
        next_start = rows[i + 1]['start'] if i + 1 < len(rows) else plan['duration_seconds'] - 2
        # Keep 100ms separation; never borrow across a shot cut or consume the tail.
        ceiling = min(shot_end, next_start - .10 if i + 1 < len(rows) else next_start)
        if required > ceiling or required - end > .25:
            continue  # Existing compositor rejects safely; no model retry for supplied audio.
        changes.append({'index': i, 'old_end': end, 'new_end': required,
                        'reason': 'reuse_existing_pause_for_compositor_reserve',
                        'source_seconds': seconds, 'tempo': 1})
        row['end'] = required
    return changes
