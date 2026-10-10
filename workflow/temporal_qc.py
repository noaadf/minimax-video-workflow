"""Full-frame, low-resolution temporal triage. Never a semantic approval gate."""
import json
import math
import re
import subprocess
import time
from pathlib import Path

from . import media
from .closing_audio import pcm, rms
from .core.io import digest, sha256, write_json
from .review_state import mark_review_required

VERSION = 'temporal-triage-v4-video-duration'


def retain_exceptions(editorial, report, plan):
    """Keep deterministic exceptions visible even if sampled model review passes."""
    if not report['exceptions']:
        return editorial
    review = mark_review_required(editorial)
    for flag in report['exceptions']:
        cursor=0; ids=[]
        for s in plan['shots']:
            if cursor <= flag['start'] < cursor+s['duration_seconds']: ids.append(s['id'])
            cursor+=s['duration_seconds']
        review.setdefault('findings',[]).append({'severity':'medium',
            'category':'audio' if flag['kind'] in ('missing_voice_energy','decoded_peak_above_full_scale') else 'unknown',
            'issue':flag['reason'], 'shot_ids':ids, 'origin':'programmatic_temporal_triage',
            'time_seconds':flag['start'], 'requires_interpretation':True})
    return editorial


def parse_frames(text):
    rows = []
    for line in text.splitlines():
        match = re.match(r'frame:(\d+)\s+pts:\S+\s+pts_time:([\d.eE+-]+)', line)
        if match:
            rows.append({'frame': int(match[1]), 'time': float(match[2])})
        elif rows and line.startswith('lavfi.') and '=' in line:
            key, value = line.split('=', 1)
            if key in ('lavfi.signalstats.YDIF', 'lavfi.signalstats.YHIGH',
                       'lavfi.signalstats.YLOW', 'lavfi.scd.score'):
                rows[-1][key.rsplit('.', 1)[-1]] = float(value)
    if not rows or any(not all(k in r for k in ('YDIF', 'YHIGH', 'YLOW', 'score')) for r in rows):
        raise ValueError('Missing full-frame temporal measurements')
    if any(b['time'] <= a['time'] for a, b in zip(rows, rows[1:])):
        raise ValueError('Nonmonotonic decoded timestamps')
    return rows


def intervals(rows, predicate, end, minimum=0):
    found = []; start = None
    for row in rows + [{'time': end, '_end': True}]:
        matched = not row.get('_end') and predicate(row)
        if matched and start is None:
            start = row['time']
        elif not matched and start is not None:
            if row['time'] - start >= minimum - 1e-5:
                found.append({'start': round(start, 4), 'end': round(row['time'], 4),
                              'duration': round(row['time']-start, 4)})
            start = None
    return found


def classify(rows, duration, cuts, fps):
    tolerance = 1.5 / fps
    def planned(t):
        return any(abs(t - cut) <= tolerance for cut in cuts)
    scenes = [{'time': r['time'], 'score': r['score'], 'planned': planned(r['time'])}
              for r in rows if r['score'] >= 12]
    flags = [{'kind': 'unplanned_scene_change', 'start': x['time'], 'end': x['time'],
              'priority': 2, 'reason': 'Large scene change outside declared edit times; inspect, not automatic rejection.'}
             for x in scenes if not x['planned'] and .2 < x['time'] < duration-.2]
    black = intervals(rows, lambda r: r['YHIGH'] <= 18, duration)
    for x in black:
        if x['start'] > .2 and x['end'] < duration-.2:
            flags.append(dict(x, kind='near_black_region', priority=1,
                              reason='Central picture region is nearly black; may be an intentional dark scene.'))
    bright = intervals(rows, lambda r: r['YLOW'] >= 230, duration)
    for x in bright:
        if x['duration'] <= 3/fps and not planned(x['start']) and .2 < x['start'] < duration-.2:
            flags.append(dict(x, kind='brief_bright_flash', priority=1,
                              reason='One-to-three-frame bright field away from declared cut.'))
    static = intervals(rows[1:], lambda r: r['YDIF'] < .1, duration, minimum=1.5)
    return {'scene_changes': scenes, 'near_black_intervals': black,
            'near_uniform_bright_intervals': bright, 'static_intervals': static, 'exceptions': flags}


def audio_presence(voice, plan):
    rows = []
    for n in plan.get('narration', []):
        a, b = n['start'], n['end']
        level = rms(voice, a, b)
        rows.append({'start': a, 'end': b, 'text': n['text'], 'rms_dbfs': round(level, 2),
                     'voice_energy_present': level > -45})
    return rows


def scan(video, folder, plan, *, intentional_cuts=None, voice_path=None):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    cuts = []; cursor = 0
    for s in plan['shots'][:-1]:
        cursor += s['duration_seconds']; cuts.append(cursor)
    cuts = sorted(set(cuts + list(intentional_cuts or [])))
    binding = {'version': VERSION, 'video_sha256': sha256(video), 'cuts': cuts,
               'narration': plan.get('narration', []),
               'voice_sha256': sha256(voice_path) if voice_path else None}
    key = digest(binding); dest = folder / (key + '.json')
    if dest.exists():
        return json.loads(dest.read_text(encoding="utf-8"))
    start = time.monotonic()
    info = media.probe(video); v = next(x for x in info['streams'] if x['codec_type'] == 'video')
    num, den = map(float, v['avg_frame_rate'].split('/')); fps = num / den
    duration = float(info['format']['duration'])
    # Exclude top labels/bottom subtitles; spatial downsampling only, no frames skipped.
    filters = ('crop=iw:ih*0.64:0:ih*0.14,scale=160:180,signalstats,scdet=t=12,'
               'metadata=mode=print:file=-')
    process = subprocess.run(['ffmpeg', '-v', 'error', '-threads', '2', '-i', str(video),
        '-an', '-vf', filters, '-f', 'null', '-'], capture_output=True, text=True, timeout=120)
    if process.returncode:
        raise RuntimeError(process.stderr[-2000:])
    rows = parse_frames(process.stdout)
    result = classify(rows, duration, cuts, fps)
    voice = audio_presence(pcm(voice_path), plan) if voice_path else []
    for n in voice:
        if not n['voice_energy_present']:
            result['exceptions'].append({'kind': 'missing_voice_energy', 'start': n['start'],
                'end': n['end'], 'priority': 1, 'reason': 'No significant voice energy in planned narration interval.'})
    audio_rate=int(next(x for x in info['streams'] if x['codec_type']=='audio')['sample_rate'])
    mix = pcm(video, channels=2, rate=audio_rate)
    peak_index, peak = max(enumerate(mix), key=lambda x: abs(x[1]), default=(0,0))
    result['mix_peak_dbfs'] = round(20*math.log10(max(1e-12, abs(peak))), 3)
    result['mix_peak_seconds'] = round(peak_index/(2*audio_rate), 4)
    result['mix_peak_sample_rate'] = audio_rate
    if abs(peak)>1:
        result['exceptions'].append({'kind':'decoded_peak_above_full_scale',
            'start':result['mix_peak_seconds'],'end':result['mix_peak_seconds'],'priority':1,
            'reason':'Decoded stereo PCM exceeds full scale; AAC overshoot may clip on fixed-point playback. Not proof of audible distortion.'})
    measured_duration = len(mix)/(2*audio_rate)
    frame_count_matches = v.get('nb_frames') is None or int(v['nb_frames']) == len(rows)
    target_duration=float(plan.get('duration_seconds',duration))
    expected_frames=round(target_duration*fps)
    video_duration_matches=media.video_duration_matches(v,target_duration)
    decoded_timeline_complete=abs(len(rows)-expected_frames)<=1
    result.update(video_duration_matches=video_duration_matches,expected_frames=expected_frames,decoded_timeline_complete=decoded_timeline_complete,binding=binding, input_digest=key, video=str(Path(video).resolve()),
        frames_scanned=len(rows), frame_count_matches=frame_count_matches, fps=fps,
        duration_seconds=duration, mix_duration_matches=abs(measured_duration-duration)<.1,
        narration_presence=voice, declared_cuts=cuts, elapsed_seconds=round(time.monotonic()-start, 3),
        status='exceptions_found' if result['exceptions'] else 'no_threshold_anomalies',
        technical_scan_passed=frame_count_matches and video_duration_matches and decoded_timeline_complete and abs(measured_duration-duration)<.1,
        api_calls=0, content_approved=False,
        not_assessed=['geometry correctness or slow morphing', 'all tiny spatial defects',
                      'transcript accuracy or pronunciation', 'artistic pacing or music quality'],
        notes=['Static intervals are informational; locked views/diagrams can be intentional.',
               'No threshold anomaly is not proof of temporal geometry or editorial quality.'])
    result['frame_metrics_path'] = str(folder / (key + '.frames.json'))
    write_json(result['frame_metrics_path'], rows)
    write_json(dest, result)
    return result
