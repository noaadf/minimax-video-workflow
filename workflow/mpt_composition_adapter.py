"""Opt-in MPT compositor. No queue writes, generation, or delivery approval.

The frozen adapter manifest is a composition contract, not the source production
manifest. It binds that manifest, approved image-first provenance, ordered videos,
whole narration, one music track, plain timed captions, and authored disclosures.
Call compose(manifest_path, manifest_sha256, output_dir, repository, python).
The Python interpreter must already provide the pinned MPT dependencies.
"""
import argparse
import fcntl
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

PINNED_COMMIT = '9db984c8befdcadccc3ee4b2a355e16e1595e0fa'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def bound(asset):
    p = Path(asset['path']).resolve()
    if sha(p) != asset['sha256']:
        raise ValueError('Asset SHA mismatch: ' + str(p))
    return str(p)


def _require_preflight(m, plan, AUTHORED_FIELDS, validate_edl):
    acceptance_path = bound(m['acceptance'])
    if plan.get('approved_story_preflight') != m['acceptance']:
        raise ValueError('Source manifest preflight binding differs')
    acceptance = json.loads(Path(acceptance_path).read_text(encoding="utf-8"))
    if acceptance.get('status') != 'ready_for_generation':
        raise ValueError('Existing preflight did not pass')
    for key in ('manifest', 'visual_review', 'edl', 'preview'):
        bound({'path':acceptance[key+'_path'], 'sha256':acceptance[key+'_sha256']})
    preflight = json.loads(Path(acceptance['manifest_path']).read_text(encoding="utf-8"))
    if any(plan.get(k) != preflight.get(k) for k in AUTHORED_FIELDS):
        raise ValueError('Authored content changed after board review')
    visual = json.loads(Path(acceptance['visual_review_path']).read_text(encoding="utf-8"))
    if visual.get('status') != 'automatic_review_passed' or not visual.get('receipt', {}).get('model'):
        raise ValueError('Completed board model review required')
    validate_edl(json.loads(Path(acceptance['edl_path']).read_text(encoding="utf-8")))


def _require_shot_images(plan):
    from PIL import Image
    for shot in plan['shots']:
        images = set(shot.get('reference_images', [])) | {shot['previsual_path']}
        for image in images:
            bound({'path':image,'sha256':plan['input_sha256'][image]})
            with Image.open(image) as im:
                im.verify()


def _require_production_results(m, plan, results, AUTHORED_FIELDS, closing, sqlite3):
    with closing(sqlite3.connect('file:'+str(Path(m['queue_path']).resolve())+'?mode=ro',uri=True)) as db:
        job = db.execute('SELECT manifest FROM jobs WHERE id=?',(plan['id'],)).fetchone()
        runtime = json.loads(job[0]) if job else {}
        fields = set(AUTHORED_FIELDS) | {'id','root_job_id','parent_job_id','approved_story_preflight'}
        if not job or any(runtime.get(k) != plan.get(k) for k in fields):
            raise ValueError('Source job authored manifest/identity mismatch')
        for p,h in runtime.get('input_sha256',{}).items():
            bound({'path':p,'sha256':h})
        for tid, result in results.items():
            row = db.execute('SELECT status,result FROM tasks WHERE id=?',(plan['id']+':'+tid,)).fetchone()
            if not row or row[0] != 'succeeded' or json.loads(row[1]) != result:
                raise ValueError('Frozen production task result mismatch: '+tid)


def _require_output_assets(m, plan, results):
    if [s['id'] for s in m['shots']] != [s['id'] for s in plan['shots']]:
        raise ValueError('Complete ordered source shots required')
    def output_asset(tid, field):
        result = results[tid]; p=result[field]
        return {'path':p,'sha256':result['sha256'][p]}
    for s in m['shots']:
        if s['video'] != output_asset(s['id'],'video_path'):
            raise ValueError('Shot binding differs from actual result')
        bound(s['video'])
    for name in ('voice','music'):
        if m[name] != output_asset(name,'audio_path'):
            raise ValueError('Audio binding differs from actual result')
        bound(m[name])


def _require_caption_timeline(m):
    for row in m['captions'] + m['disclosures']:
        if not isinstance(row['text'],str) or not row['text'].strip() or re.search(r'[{}<>\\\r\n]',row['text']):
            raise ValueError('Plain single-line caption/disclosure text required')
        if not 0 <= row['start'] < row['end'] <= m['duration_seconds']:
            raise ValueError('Caption/disclosure timing outside film')
    covered=0.0
    for row in sorted(m['disclosures'],key=lambda x:x['start']):
        if row['start'] > covered: raise ValueError('Disclosure coverage has a gap')
        covered=max(covered,row['end'])
    if covered + .01 < m['duration_seconds']: raise ValueError('Disclosure must cover whole film')


def _require_whole_voice(plan, results):
    narration = plan.get('narration', [])
    segments = results['voice'].get('segments', [])
    if len(narration) != 1 or len(segments) != 1 or segments[0]['text'] != narration[0]['text']:
        raise ValueError('One complete narration and exact voice receipt required')
    segment = segments[0]
    if segment.get('source_path') != narration[0].get('audio_path') or segment.get('source_sha256') != plan['input_sha256'].get(segment['source_path']):
        raise ValueError('Whole voice source receipt binding mismatch')
    bound({'path':segment['source_path'],'sha256':segment['source_sha256']})


def production_inputs(m):
    """Validate the existing rollout schemas, never mint approval receipts."""
    from workflow.narrative_gate import require_review, AUTHORED_FIELDS
    from workflow.storyboard import enabled, missing_previsuals
    from workflow.roughcut import validate as validate_edl
    from contextlib import closing
    import sqlite3
    source = bound(m['source_manifest'])
    plan = json.loads(Path(source).read_text(encoding="utf-8"))
    require_review(source, bound(m['narrative_review']))
    if not enabled(plan) or missing_previsuals(plan):
        raise ValueError('Approved complete image-first storyboard required')
    _require_preflight(m, plan, AUTHORED_FIELDS, validate_edl)
    for p, h in plan.get('input_sha256', {}).items():
        bound({'path':p,'sha256':h})
    _require_shot_images(plan)
    # Compare frozen asset task receipts with the actual read-only production DB.
    results = json.loads(Path(bound(m['task_results'])).read_text(encoding="utf-8"))
    _require_production_results(m, plan, results, AUTHORED_FIELDS, closing, sqlite3)
    _require_output_assets(m, plan, results)
    _require_whole_voice(plan, results)
    if m['duration_seconds'] != plan['duration_seconds']:
        raise ValueError('Duration differs from frozen source')
    if Path(m['caption_source']['path']).resolve() != Path(results['compose']['video_path']).parent/'captions.ass':
        raise ValueError('Caption source must be existing compose artifact')
    if m.get('fit_mode') != 'contain':
        raise ValueError('Preserve complete source framing; no implicit cover crop')
    return plan


def parse_captions(path, duration):
    def seconds(t):
        h, mi, sec = t.split(':'); return int(h)*3600 + int(mi)*60 + float(sec)
    captions=[]; disclosures=[]
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.startswith('Dialogue: '): continue
        a=line[10:].split(',',9)
        row={'start':seconds(a[1]),'end':min(seconds(a[2]),duration),'text':re.sub(r'\{[^}]*\}','',a[9]).replace(r'\N',' ')}
        if a[3]=='Main': captions.append(row)
        elif a[3] in ('Label','ShotLabel'):
            row['role']=a[3]; disclosures.append(row)
    return captions, disclosures


def validate(path, expected):
    if sha(path) != expected:
        raise ValueError('Frozen composition manifest SHA mismatch')
    m=json.loads(Path(path).read_text(encoding="utf-8"));production_inputs(m)
    caption_source = bound(m['caption_source'])
    if parse_captions(caption_source, m['duration_seconds']) != (m['captions'],m['disclosures']):
        raise ValueError('Caption/disclosure timeline differs from frozen ASS source')
    if not m.get('captions') or not m.get('disclosures'):
        raise ValueError('Timed captions and authored AI/diagram disclosures required')
    _require_caption_timeline(m)
    if any(len(r['text']) > 32 for r in m['captions']):
        raise ValueError('Split captions into timed short phrases before composing')
    return m


def freeze_from_request(request_path, queue_path, output_dir):
    """Read existing rollout/job receipts and freeze adapter inputs; no render/queue write."""
    import sqlite3
    from contextlib import closing
    request=json.loads(Path(request_path).read_text(encoding="utf-8"));out=Path(output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    def asset(p):return {'path':str(Path(p).resolve()),'sha256':sha(p)}
    plan=json.loads(Path(request['manifest_path']).read_text(encoding="utf-8"))
    with closing(sqlite3.connect('file:'+str(Path(queue_path).resolve())+'?mode=ro',uri=True)) as db:
        ids=[s['id'] for s in plan['shots']]+['voice','music','compose']
        results={}
        for tid in ids:
            row=db.execute('SELECT status,result FROM tasks WHERE id=?',(plan['id']+':'+tid,)).fetchone()
            if not row or row[0]!='succeeded': raise ValueError('Asset task not ready: '+tid)
            results[tid]=json.loads(row[1])
    result_path=out/'task_results.json';data=json.dumps(results,ensure_ascii=False,indent=2)
    if result_path.exists() and result_path.read_text(encoding="utf-8")!=data:raise ValueError('Existing frozen task results differ')
    result_path.write_text(data, encoding="utf-8")
    def output_asset(tid,field):
        r=results[tid];p=r[field];return {'path':p,'sha256':r['sha256'][p]}
    ass=Path(results['compose']['video_path']).parent/'captions.ass'
    captions, disclosures = parse_captions(ass, plan['duration_seconds'])
    m={'source_manifest':asset(request['manifest_path']),'narrative_review':asset(request['review_path']),'acceptance':asset(request['preflight_path']),'task_results':asset(result_path),'queue_path':str(Path(queue_path).resolve()),'shots':[{'id':s['id'],'video':output_asset(s['id'],'video_path')} for s in plan['shots']],'voice':output_asset('voice','audio_path'),'music':output_asset('music','audio_path'),'title':plan['title'],'duration_seconds':plan['duration_seconds'],'fit_mode':'contain','captions':captions,'disclosures':disclosures,'caption_source':asset(ass)}
    p=out/'composition_manifest.json';data=json.dumps(m,ensure_ascii=False,indent=2)
    if p.exists() and p.read_text(encoding="utf-8")!=data:raise ValueError('Existing frozen composition differs')
    p.write_text(data);validate(p,sha(p));return {'manifest_path':str(p),'manifest_sha256':sha(p)}


def ass_text(m):
    def t(sec):
        cs = round(sec * 100)
        return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'
    header = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,Heiti SC,56,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,3,1,2,70,70,140,1
Style: ShotDisclosure,Heiti SC,28,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,2,1,8,48,48,112,1
Style: Disclosure,Heiti SC,30,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,2,1,8,48,48,65,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    rows = []
    for style, items in [('Main', m['captions']), ('Disclosure', m['disclosures'])]:
        for r in items:
            row_style = 'ShotDisclosure' if r.get('role') == 'ShotLabel' else style
            width = 16 if style == 'Main' else 26
            text = r'\N'.join(r['text'][i:i+width] for i in range(0, len(r['text']), width))
            rows.append(f"Dialogue: 0,{t(r['start'])},{t(r['end'])},{row_style},,0,0,0,,{text}")
    return header + '\n'.join(rows) + '\n'


def compose(manifest_path, manifest_sha256, output_dir, repository, python):
    """Return receipt; repeated identical successful invocation does not rerender."""
    m = validate(manifest_path, manifest_sha256)
    repo = Path(repository).resolve()
    commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != PINNED_COMMIT or subprocess.check_output(['git', '-C', str(repo), 'diff', 'HEAD', '--', 'app'], text=True):
        raise ValueError('Pinned unmodified native MPT app required')
    out = Path(output_dir).resolve(); out.mkdir(parents=True, exist_ok=True)
    with (out / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt_path = out / 'receipt.json'
        if receipt_path.exists():
            r = json.loads(receipt_path.read_text(encoding="utf-8"))
            if r['manifest_sha256'] != manifest_sha256:
                raise ValueError('Output directory belongs to another manifest')
            if sha(r['output_path']) == r['output_sha256']:
                return r
            raise ValueError('Existing output changed; preserve and investigate')
        (out / 'captions.ass').write_text(ass_text(m), encoding="utf-8")
        cmd = [str(python), str(Path(__file__).resolve()), '--worker', str(Path(manifest_path).resolve()), manifest_sha256, str(out), str(repo)]
        with (out / 'native.log').open('w') as log:
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
        r = json.loads((out / 'worker_receipt.json').read_text(encoding="utf-8"))
        final = out / 'final.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(out/'native.mp4'), '-vf', 'ass=captions.ass', '-c:v', 'libx264', '-crf', '18', '-c:a', 'copy', str(final)], cwd=out, check=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(final), '-f', 'null', '-'], check=True)
        # Revalidate after processing: changed inputs cannot acquire a success receipt.
        validate(manifest_path, manifest_sha256)
        r.update(adapter_sha256=sha(__file__), native_log_sha256=sha(out/'native.log'), overlay_sha256=sha(out/'captions.ass'), manifest_sha256=manifest_sha256, repository_commit=commit, output_path=str(final), output_sha256=sha(final), full_decode_passed=True, audio_listening_review='not_performed', delivery_approved=False, command=cmd)
        tmp = out/'receipt.tmp'; tmp.write_text(json.dumps(r, ensure_ascii=False, indent=2)); tmp.replace(receipt_path, encoding="utf-8")
        return r


def worker(path, expected, output, repository):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    m = validate(path, expected)
    sys.path.insert(0, repository)
    from app.services import video
    from app.config import config
    from app.models.schema import VideoParams, VideoAspect, VideoConcatMode, VideoFitMode
    config.app['video_codec'] = 'libx264'
    def duration(p):
        return float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',p],text=True))
    out=Path(output); clips=[bound(s['video']) for s in m['shots']]; voice=bound(m['voice']); music=bound(m['music'])
    if abs(duration(voice)-m['duration_seconds']) > .1:
        raise ValueError('Frozen duration does not match whole narration')
    padding=math.ceil((max(.1,duration(voice)+.1-sum(duration(p) for p in clips))+len(clips)*2/30)*30)/30
    if padding > 1:
        raise ValueError('Material deficit exceeds safety padding; do not conceal missing action')
    subprocess.run(['ffmpeg','-v','error','-y','-i',clips[-1],'-an','-vf',f'tpad=stop_mode=clone:stop_duration={padding}','-c:v','libx264','-crf','18',str(out/'tail.mp4')],check=True)
    clips[-1]=str(out/'tail.mp4')
    video.combine_videos(str(out/'combined.mp4'),clips,voice,video_aspect=VideoAspect.portrait,video_concat_mode=VideoConcatMode.sequential,max_clip_duration=100,threads=2,clip_speed=1.0,video_fit_mode=VideoFitMode.contain)
    params=VideoParams(video_subject=m['title'],video_aspect=VideoAspect.portrait,bgm_type='custom',bgm_file=music,bgm_volume=.12,voice_volume=1.0,subtitle_enabled=False,n_threads=2)
    ok=video.generate_video(str(out/'combined.mp4'),voice,'',str(out/'native.mp4'),params,bgm_file_override=music)
    if not ok: raise RuntimeError('Native background music mixing failed')
    import importlib.metadata
    (out/'worker_receipt.json').write_text(json.dumps({'python_version':sys.version,'moviepy_version':importlib.metadata.version('moviepy'),'native_module_sha256':sha(video.__file__),'tail_padding_seconds':padding,'bgm_mix_succeeded':ok,'native_output_sha256':sha(out/'native.mp4')}), encoding="utf-8")


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--worker',nargs=4);a=p.parse_args()
    if not a.worker:p.error('Use compose() from the opt-in production integration')
    worker(*a.worker)
