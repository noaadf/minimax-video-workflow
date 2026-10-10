"""Cheap deterministic triage. These checks never certify semantic geometry."""
import math
import re
import subprocess
from pathlib import Path
from . import media
from .core.io import digest, sha256, write_json

VERSION='programmatic-qc-v4-review-scope'
REVIEW_SCOPE_VERSION = 'programmatic-review-scope-v1'
SEMANTIC_CRITERIA = ('narrative_continuity', 'composition', 'action_continuity')
# Only exact measured checks cover these dimensions. Timeline arithmetic is not
# subtitle/audio alignment; energy checks are not a subjective listening review.
DIMENSION_CHECKS = {
    'duration': ('duration', 'video_duration'),
    'shot_timing': ('shot_durations_positive', 'shot_timeline_complete'),
    'narration_timing': ('narration_timeline_valid',),
    'vertical': ('vertical',),
    'black_frames': ('screening_decode',),
    'volume': ('volume',),
    'subtitle_alignment': ('subtitle_alignment',),
}


def llm_review_scope(report, criteria=None, *, stage='final', video_sha256=None):
    """Filter a request using same-video measurements; never call an API.

    The consumer must include this scope in its prompt/payload and cache key.
    Production review.py currently does not consume it. Unmeasured dimensions
    stay pending for programmatic QC rather than being certified by a model.
    """
    if stage not in ('preflight', 'final'):
        raise ValueError('Unknown review stage')
    requested = list(dict.fromkeys(SEMANTIC_CRITERIA if criteria is None else criteria))
    if any(not isinstance(c, str) or not c.strip() for c in requested):
        raise ValueError('Review criteria must be nonempty strings')
    binding = report.get('binding', {})
    bound = bool(binding.get('video_sha256'))
    if video_sha256 is not None:
        bound = bound and binding['video_sha256'] == video_sha256
    checks = report.get('checks', {})
    passed = [name for name, required in DIMENSION_CHECKS.items()
              if bound and all(checks.get(key) is True for key in required)
              and (name != 'black_frames' or report.get('interior_black_intervals') == [])]
    excluded = [c for c in requested if c in passed]
    pending = [c for c in requested if c in DIMENSION_CHECKS and c not in passed]
    deferred = [c for c in requested if stage == 'preflight'
                and c in ('action_continuity', 'action_result', 'motion', 'continuous_motion', 'audio', 'listening')]
    blocked = report.get('hard_failed') is True or not bound
    remaining = [c for c in requested if c not in DIMENSION_CHECKS and c not in deferred]
    return {'version': REVIEW_SCOPE_VERSION, 'review_stage': stage,
            'video_sha256': binding.get('video_sha256'), 'evidence_binding_valid': bound,
            'criteria': [] if blocked else remaining,
            'excluded_programmatic_dimensions': excluded,
            'programmatic_passed_dimensions': passed,
            'pending_programmatic_dimensions': pending, 'deferred_checks': deferred,
            'model_calls_blocked': blocked,
            'model_review_needed': bool(remaining) and not blocked,
            'content_approved': False}


def timeline_checks(plan):
    duration=float(plan.get('duration_seconds',30))
    shots=plan.get('shots',[])
    lengths=[float(s.get('duration_seconds',0)) for s in shots]
    checks={'shot_durations_positive':bool(lengths) and all(math.isfinite(x) and x>0 for x in lengths),
            'shot_timeline_complete':abs(sum(lengths)-duration)<.05}
    segments=plan.get('narration',[]);previous=0;valid=True
    for s in segments:
        start,end=s.get('start'),s.get('end')
        if type(start) not in (float,int) or type(end) not in (float,int) or not (math.isfinite(start) and math.isfinite(end) and previous<=start<end<=duration):
            valid=False;break
        previous=end
    checks['narration_timeline_valid']=valid
    return checks


def screen_video(video, folder, plan):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    binding={'version':VERSION,'video_sha256':sha256(video),'timeline':{k:plan.get(k) for k in ('duration_seconds','shots','narration')}}
    key=digest(binding);dest=folder/(key+'.json')
    if dest.exists():return __import__('json').loads(dest.read_text(encoding="utf-8"))
    info=media.probe(video);v=next(s for s in info['streams'] if s['codec_type']=='video')
    length=float(info['format']['duration']);target=float(plan.get('duration_seconds',30))
    checks=timeline_checks(plan)
    checks.update(duration=abs(length-target)<.1,video_duration=media.video_duration_matches(v,target),vertical=v['height']>v['width'])
    # Decode once to a tiny 2fps stream; pure programmatic measurements, no API.
    cmd=['ffmpeg','-v','info','-threads','2','-i',str(video),'-an','-vf',
         'fps=2,scale=160:-2,blackdetect=d=0.45:pix_th=0.02:pic_th=0.98',
         '-f','framemd5','-']
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
    checks['screening_decode']=p.returncode==0
    black=[{'start':float(a),'end':float(b),'duration':float(d)} for a,b,d in re.findall(r'black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)',p.stderr)]
    # Intentional start/end fades are not automatically errors.
    interior=[x for x in black if (x['start']>.25 and x['end']<length-.25)
              or x['duration']>=max(.5,length*.8)]
    hashes=[line.rsplit(',',1)[-1].strip() for line in p.stdout.splitlines() if line and not line.startswith('#')]
    repeat=sum(a==b for a,b in zip(hashes,hashes[1:]))/max(1,len(hashes)-1)
    report={'binding':binding,'checks':checks,'hard_failed':not all(checks.values()),
            'black_intervals':black,'interior_black_intervals':interior,'identical_adjacent_sample_fraction':repeat,
            'status':'failed' if not all(checks.values()) else 'anomaly' if interior else 'passed',
            'content_approved':False,'limits':['2fps black-frame screening; sub-0.5s defects can be missed',
            'Repeated frames can be intentional locked shots; reported only, not rejected',
            'No semantic, structure or audio-quality conclusion']}
    # Black anomalies stay in the existing interpretation lane, not a new hard
    # gate. A successful decode with no anomaly is enough to avoid duplicate QC.
    report['llm_review_scope'] = llm_review_scope(report)
    write_json(dest,report);return report
