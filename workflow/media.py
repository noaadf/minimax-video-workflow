"""Deterministic FFmpeg postproduction with independent narration/music stems."""
import array
import json
import math
import os
import re
import subprocess
import hashlib
from pathlib import Path
from .code_identity import code_identity

NORMALIZATION_VERSION='whole_action_v1'
MEDIA_CODE_SHA256=code_identity(__file__)


def run(*args, cwd=None, timeout=900):
    p = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors='replace')[-4000:])
    return p.stdout


def ff(*args, cwd=None):
    return run('ffmpeg', '-y', '-v', 'error', '-threads', '2', *args, cwd=cwd)


def probe(path):
    return json.loads(run('ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path))


def duration(path):
    return float(probe(path)['format']['duration'])


def audio_rms(path, start, length):
    raw = run('ffmpeg', '-v', 'error', '-ss', str(start), '-i', path, '-t', str(length),
              '-vn', '-ar', '16000', '-ac', '1', '-f', 'f32le', '-')
    values = array.array('f', raw)
    return 20 * math.log10(max(1e-12, math.sqrt(sum(v*v for v in values) / max(1,len(values)))))


def escape_ass(text):
    return text.replace('\\', '').replace('{', '').replace('}', '').replace('\n', '\\N')


def timestamp(x):
    cs = round(x * 100)
    return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'


def shot_label_events(plan):
    """Bind editorial labels to the actual cumulative shot timeline."""
    labels = plan.get('postproduction_labels', [])
    if not isinstance(labels, list):
        raise ValueError('postproduction_labels must be a list')
    if not labels:
        return []
    ranges = {}; start = 0
    for shot in plan.get('shots', []):
        seconds = shot.get('duration_seconds')
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('invalid shot duration for postproduction label')
        if shot['id'] in ranges:
            raise ValueError('ambiguous shot id for postproduction label')
        ranges[shot['id']] = (start, start + seconds)
        start += seconds
    grouped = {}
    for label in labels:
        if not isinstance(label, dict) or label.get('shot_id') not in ranges:
            raise ValueError('postproduction label references unknown shot_id')
        text = label.get('text')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('postproduction label text must be nonempty')
        if label.get('generated_in_model', False) is not False:
            raise ValueError('postproduction labels must not be generated in model')
        # Single-line text cannot collide with the disclosure above it.
        text = ' '.join(text.split())
        grouped.setdefault(label['shot_id'], []).append(text)
    return [{'start': ranges[sid][0], 'end': ranges[sid][1],
             'text': ' · '.join(texts)} for sid, texts in grouped.items()]


def captions(plan, dest, cover=False):
    local_labels = shot_label_events(plan)
    # ASS uses BBGGRR; 00D7FF is warm yellow. Text remains clear of Douyin's right rail/bottom UI.
    head = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,Heiti SC,76,&H0000D7FF,&H0000D7FF,&H00141414,&H70000000,-1,0,0,0,100,100,0,0,1,5,1,2,90,145,340,1
Style: Label,Heiti SC,26,&H80FFFFFF,&H80FFFFFF,&HC0202020,&HFF000000,0,0,0,0,100,100,0,0,1,1,0,7,48,48,65,1
Style: ShotLabel,Heiti SC,32,&H20FFFFFF,&H20FFFFFF,&H00141414,&H70000000,0,0,0,0,100,100,0,0,1,2,0,7,48,145,165,1
Style: Title,Heiti SC,118,&H0000D7FF,&H0000D7FF,&H00141414,&H70000000,-1,0,0,0,100,100,0,0,1,7,2,5,70,100,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    font = os.environ.get('WORKFLOW_FONT_FAMILY', 'Heiti SC')
    if not font or any(c in font for c in ',\r\n'):
        raise ValueError('invalid subtitle font family')
    head = head.replace(',Heiti SC,', ',' + font + ',')
    scale = float(os.environ.get('WORKFLOW_FONT_SCALE', '1'))
    if not math.isfinite(scale) or not .5 <= scale <= 2:
        raise ValueError('subtitle font scale must be 0.5..2')
    head = re.sub(r'(Style: [^,]+,[^,]+,)(\d+)',
                  lambda m: m[1] + str(round(int(m[2]) * scale)), head)
    rows = [f"Dialogue: 1,0:00:00.00,{timestamp(plan.get('duration_seconds',30))},Label,,0,0,0,,AI演绎 · 非现场实拍"]
    from .disclosures import normalize
    for disclosure in normalize(plan.get('visual_disclosures', []), plan.get('duration_seconds', 30)):
        rows.append(f"Dialogue: 1,{timestamp(disclosure['start'])},{timestamp(disclosure['end'])},Label,,0,0,0,," +
                    r'{\pos(48,110)}' + escape_ass(disclosure['text']))
    if cover:
        text = plan['cover_title']
        if len(text) > 8:
            text = text[:len(text)//2] + '\n' + text[len(text)//2:]
        rows.append(f"Dialogue: 2,0:00:00.00,{timestamp(plan.get('duration_seconds',30))},Title,,0,0,0,," + escape_ass(text))
    else:
        for label in local_labels:
            rows.append(f"Dialogue: 1,{timestamp(label['start'])},{timestamp(label['end'])},ShotLabel,,0,0,0,,{escape_ass(label['text'])}")
        from .caption_timing import events
        for event in events(plan):
            rows.append(f"Dialogue: 0,{timestamp(event['start'])},{timestamp(event['end'])},Main,,0,0,0,,{escape_ass(event.get('display_text', event['text']))}")
    Path(dest).write_text(head + '\n'.join(rows) + '\n', encoding="utf-8")


def whole_action_filter(src, seconds, start=0):
    """Map the complete generated action into its slot, including the ending frame."""
    if start!=0:raise ValueError('whole_action requires the complete source from zero')
    info=probe(src);video=next((s for s in info['streams'] if s['codec_type']=='video'),None)
    if video is None:raise ValueError('whole_action source has no video')
    source_seconds=float(video.get('duration') or info['format']['duration'])
    if not math.isfinite(source_seconds) or source_seconds<=0 or not math.isfinite(seconds) or seconds<=0:
        raise ValueError('invalid whole_action duration')
    speed=source_seconds/seconds
    if not 1/1.5<=speed<=1.5:
        raise ValueError('whole_action timing exceeds natural speed bounds; retime storyboard')
    return f'setpts=PTS-STARTPTS,setpts={seconds/source_seconds:.12f}*PTS'

def normalize_shot(src, dest, seconds, start=0, *, fit_policy='source_window', framing_policy=None):
    from .framing import filter_for
    framing_filter = filter_for(1080, 1920, framing_policy)
    info = probe(src)
    if not any(s['codec_type'] == 'video' for s in info['streams']):
        raise ValueError('generated artifact has no video')
    if fit_policy not in ('source_window','whole_action'):raise ValueError('unknown shot fit policy')
    timing=whole_action_filter(src,seconds,start) if fit_policy=='whole_action' else ''
    if fit_policy=='source_window' and duration(src) + .06 < start + seconds:
        raise ValueError('source too short; refuse frozen padding or silent timing change')
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    exact_duration = abs(float(info['format']['duration']) - seconds) < .001
    # Revision assets already have this contract. Preserve bytes and avoid another lossy encode.
    if (start == 0 and exact_duration and video.get('codec_name') == 'h264'
            and (video.get('width'), video.get('height')) == (1080, 1920)
            and video.get('avg_frame_rate') == '24/1' and video.get('pix_fmt') == 'yuv420p'
            and video.get('sample_aspect_ratio') == '1:1'
            and not video.get('side_data_list')
            and len(info['streams']) == 1):
        import shutil
        shutil.copyfile(src, dest)
        return str(dest)
    vf = framing_filter
    if timing:vf=timing+','+vf
    ff('-ss', start, '-i', src, '-an', '-t', seconds, '-vf', vf,
       '-c:v','libx264','-threads','2','-preset','veryfast','-crf','20','-pix_fmt','yuv420p', dest)
    return str(dest)


def narration(plan, dest):
    import asyncio
    import copy
    import hashlib
    from .writing import shorten
    from .core.io import write_json, sha256
    working = copy.deepcopy(plan)
    folder = Path(dest).parent
    sources = {}
    repairs = []
    voice = plan.get('voice', {})
    async def synthesize(indices):
        try:
            import edge_tts
        except ImportError as e:
            raise ValueError('install edge-tts in the controller venv or supply narration audio_path') from e
        # Three media lanes already run concurrently; keep one TTS request per film.
        sem = asyncio.Semaphore(1)
        async def one(i):
            n = working['narration'][i]
            segment_voice = {**voice, **n.get('voice', {})}
            identity = hashlib.sha256(json.dumps([n['text'],segment_voice],sort_keys=True).encode()).hexdigest()[:16]
            path = folder / f'tts_{i}_{identity}.mp3'
            async with sem:
                if not path.is_file():
                    from .audio_cache import speech
                    if working.get('caption_policy') == 'speech_aligned_v1':
                        if plan.get('speech_execution') == 'cloud_ssh':
                            from .cloud_speech import synthesize_timed
                            await asyncio.to_thread(synthesize_timed,n['text'],segment_voice,path,path.with_suffix('.words.json'))
                        else:
                            from .tts_word_boundaries import save_with_timing
                            await save_with_timing(n['text'], segment_voice, path, path.with_suffix('.words.json'))
                        n['word_timing_path'] = str(path.with_suffix('.words.json'))
                        sources[i] = str(path)
                        return
                    async def produce(temp):
                        if plan.get('speech_execution') == 'cloud_ssh':
                            from .cloud_speech import synthesize
                            await asyncio.to_thread(synthesize,n['text'],segment_voice,temp)
                            return
                        await edge_tts.Communicate(n['text'], segment_voice.get('name','zh-CN-YunjianNeural'),
                            rate=segment_voice.get('rate','+0%'),pitch=segment_voice.get('pitch','-18Hz'),
                            volume=segment_voice.get('volume','+0%')).save(str(temp))
                    await speech(n['text'], segment_voice, path, produce)
            sources[i] = str(path)
            if working.get('caption_policy') == 'speech_aligned_v1':
                n['word_timing_path'] = str(path.with_suffix('.words.json'))
        await asyncio.gather(*(one(i) for i in indices))
    for i,n in enumerate(working['narration']):
        if n.get('audio_path'):
            sources[i] = n['audio_path']
    pending = [i for i in range(len(working['narration'])) if i not in sources]
    if pending:
        asyncio.run(synthesize(pending))
    from .voice_slot_repair import extend_into_pause
    timing_repairs = extend_into_pause(working, [duration(sources[i]) for i in range(len(working['narration']))])
    # At most two API revisions. Only failing lines are regenerated.
    for attempt in range(3):
        oversized = []
        for i,n in enumerate(working['narration']):
            seconds = duration(sources[i]);slot = n['end']-n['start']
            if n.get('preserve_delivery') and seconds > slot - .08 and n.get('audio_path'):
                raise ValueError(f'narration segment {i} needs a longer slot to preserve its deliberate delivery')
            limit=1.0 if n.get('preserve_delivery') else plan.get('voice_policy',{}).get('max_tempo',1.3)
            if seconds / max(.1,slot-.08) > limit:
                if n.get('audio_path'):
                    raise ValueError(f'supplied narration segment {i} does not fit; do not silently rewrite supplied audio')
                oversized.append({'index':i,'source_seconds':seconds,'slot_seconds':slot})
        if not oversized:
            break
        if working.get('pacing_policy') == 'storyboard_v1':
            raise ValueError('storyboard speech does not fit; re-time the authored plan before video generation')
        if attempt == 2:
            write_json(folder/'narration_repair.json',{'status':'failed','repairs':repairs,'remaining':oversized})
            raise ValueError('narration still exceeds slot after two bounded automatic revisions')
        write_json(folder/'narration_repair.json',{'status':'repairing','round':attempt+1,'measurements':oversized,'repairs':repairs})
        revisions, api_receipt = shorten(working,oversized)
        repairs.append({'measurements':oversized,'revisions':revisions,'api_receipt':api_receipt})
        for revision in revisions:
            n = working['narration'][revision['index']]
            n['text'] = revision['text']
            n.pop('caption_text',None)
        from .script_attribution import record
        record(working, api_receipt, 'duration_editor')
        asyncio.run(synthesize([x['index'] for x in revisions]))
    inputs, chains, labels, receipt = [], [], [], []
    for i,n in enumerate(working['narration']):
        src=sources[i];seconds=duration(src);slot=n['end']-n['start']
        tempo=1 if n.get('preserve_delivery') else max(1,seconds/max(.1,slot-.08))
        if working.get('caption_policy') == 'speech_aligned_v1':
            if not n.get('word_timing_path'):
                raise ValueError('supplied audio requires word_timing_path; no estimated character timing')
            n['audio_path'] = str(Path(src).resolve())
            n['caption_tempo'] = tempo
            from .caption_timing import events
            events({'caption_policy':'speech_aligned_v1','narration':[n]})
        inputs += ['-i',src]
        chains.append(f'[{i}:a]atempo={tempo:.5f},atrim=duration={slot},asetpts=PTS-STARTPTS,adelay={round(n["start"]*1000)}:all=1[a{i}]')
        labels.append(f'[a{i}]')
        receipt.append({'start':n['start'],'end':n['end'],'text':n['text'],'tempo':tempo,'source_seconds':seconds,
                        'source_path':str(Path(src).resolve()),'source_sha256':sha256(src),
                        'voice':{**voice,**n.get('voice',{})},'preserve_delivery':bool(n.get('preserve_delivery'))})
    chains.append(''.join(labels)+f'amix=inputs={len(labels)}:normalize=0,aresample=48000,'
                  f"apad=whole_len={round(plan.get('duration_seconds',30)*48000)},atrim=end_sample={round(plan.get('duration_seconds',30)*48000)},asetpts=N/SR/TB,"
                  'loudnorm=I=-17:TP=-2:LRA=7,aresample=48000,'
                  f"atrim=end_sample={round(plan.get('duration_seconds',30)*48000)},asetpts=N/SR/TB[out]")
    ff(*inputs,'-filter_complex',';'.join(chains),'-map','[out]','-t',str(plan.get('duration_seconds',30)),'-ar','48000','-ac','2',dest)
    write_json(folder/'narration_repair.json',{'status':'succeeded','repairs':repairs,'timing_repairs':timing_repairs,'segments':receipt,'script_authorship':working.get('script_authorship', [])})
    return {'segments':receipt,'effective_narration':working['narration'],'script_authorship':working.get('script_authorship', []),'repair_rounds':len(repairs),'timing_repairs':timing_repairs}


def _music_selection(path):
    data = Path(path).read_bytes()
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('invalid music selection evidence')
    return value, hashlib.sha256(data).hexdigest()


def _require_music_selection(plan, selection):
    """A matching audio hash cannot authorize another requested music window."""
    offset = plan['music'].get('offset_seconds', 0)
    length = plan.get('duration_seconds', 30)
    recorded = (selection.get('offset_seconds'), selection.get('span_seconds'))
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in recorded)
            or recorded != (offset, length)):
        raise ValueError(f'music selection cannot satisfy requested offset={offset}, length={length}; '
                         f'recorded selection offset={recorded[0]}, span={recorded[1]}. '
                         'Choose an explicit source offset and duration fully inside one audible span, '
                         'then render a matching selection; preserve the existing cache for diagnosis. '
                         'The requested start and duration will not be changed automatically.')


def _recover_music_pair(plan, audio, pending, receipt, identity, sha256, _valid_duration):
    """A partial publication keeps its journal and staging for explicit diagnosis."""
    cached = False
    try:
        record = json.loads(pending.read_bytes())
        seconds = record.get('duration_seconds') if isinstance(record, dict) else None
        request_bound = (isinstance(record, dict) and record.get('schema_version') == 2
                         and record.get('identity') == identity and _valid_duration(seconds)
                         and abs(seconds-plan.get('duration_seconds',30)) <= .1)
        selection_sha = None
        if audio.with_suffix('.selection.json').exists():
            try:
                selection, selection_sha = _music_selection(audio.with_suffix('.selection.json'))
            except (ValueError,KeyError): pass
        matching_audio = request_bound and audio.exists() and record.get('sha256') == sha256(audio)
        cached = matching_audio and selection_sha is not None and record.get('selection_sha256') == selection_sha
        if not cached and request_bound and 'selection_staging' in record:
            # Staging names are diagnostic only; never follow journal-supplied paths.
            phase = 'selection' if matching_audio else 'audio'
            raise RuntimeError(f'incomplete music cache {phase} commit; preserve staging and diagnose before retry')
    except (ValueError,KeyError): pass
    if cached:
        _require_music_selection(plan, selection)
        os.replace(pending,receipt)
    return cached, record if cached else None, selection if cached else None


def _cached_music(plan, audio, receipt, pending, identity, sha256, _valid_duration):
    """Admit a committed pair or promote a complete pending pair under the lock."""
    cached = False
    if audio.exists() and receipt.exists() and audio.with_suffix('.selection.json').exists():
        try:
            record = json.loads(receipt.read_bytes())
            selection, selection_sha = _music_selection(audio.with_suffix('.selection.json'))
            cached = (isinstance(record, dict) and record.get('identity') == identity
                      and record.get('sha256') == sha256(audio)
                      and record.get('schema_version', 1) in (1, 2)
                      and record.get('selection_sha256', selection_sha if record.get('schema_version', 1) == 1 else None) == selection_sha)
        except (ValueError,KeyError): pass
    # Recover a complete pair; any known partial publication requires diagnosis.
    if not cached and pending.exists():
        cached, record, selection = _recover_music_pair(plan, audio, pending, receipt, identity, sha256, _valid_duration)
    if cached:
        _require_music_selection(plan, selection)
        audio_sha = record['sha256']
    return cached, selection if cached else None, audio_sha if cached else None


def _publish_music_cache(plan, folder, audio, receipt, pending, identity, uuid, sha256, write_json):
    """Journal the verified pair before audio, selection, and receipt publication."""
    temp = folder/(uuid.uuid4().hex+'.wav')
    publishing = False
    try:
        _render_music(plan,temp)
        seconds = duration(temp)
        if not math.isfinite(seconds) or seconds <= 0 or abs(seconds-plan.get('duration_seconds',30)) > .1:
            raise ValueError('rendered music has wrong duration')
        selection, selection_sha = _music_selection(temp.with_suffix('.selection.json'))
        _require_music_selection(plan, selection)
        audio_sha = sha256(temp)
        write_json(pending,{'schema_version':2,'sha256':audio_sha,'identity':identity,
                            'selection_sha256':selection_sha,'duration_seconds':seconds,
                            'audio_staging':temp.name,
                            'selection_staging':temp.with_suffix('.selection.json').name})
        publishing = True
        os.replace(temp,audio)
        os.replace(temp.with_suffix('.selection.json'),audio.with_suffix('.selection.json'))
        os.replace(pending,receipt)
        publishing = False
    finally:
        if not publishing:
            temp.unlink(missing_ok=True)
            temp.with_suffix('.selection.json').unlink(missing_ok=True)
    return selection, audio_sha


def _publish_music_output(dest, audio, selection, audio_sha, cached, identity, shutil, uuid, sha256, write_json):
    """Prepare bound evidence before either destination file becomes visible."""
    dest = Path(dest)
    tempdest = dest.with_name(dest.name+'.'+uuid.uuid4().hex+'.part')
    tempselection = tempdest.with_name(tempdest.name+'.selection.json')
    published_audio = False
    try:
        shutil.copyfile(audio,tempdest)
        if sha256(tempdest) != audio_sha:
            raise ValueError('music output differs from verified cache audio')
        # Prepare bound evidence before replacing either destination file.
        write_json(tempselection,{**selection,'cache_hit':cached,'cache_identity':identity,
                                  'audio_sha256':audio_sha})
        os.replace(tempdest,dest)
        published_audio = True
        os.replace(tempselection,dest.with_suffix('.selection.json'))
    finally:
        tempdest.unlink(missing_ok=True)
        if not published_audio:
            tempselection.unlink(missing_ok=True)
            tempselection.with_suffix(tempselection.suffix+'.tmp').unlink(missing_ok=True)
        # After audio publication, retain any remaining evidence for diagnosis.


def music(plan, dest):
    import fcntl
    import shutil
    import uuid
    from .core.io import digest, sha256, write_json
    from .audio_cache import _valid_duration
    root = Path(os.environ.get('WORKFLOW_AUDIO_CACHE', str(Path(__file__).resolve().parents[1]/'output/production/audio_cache')))
    identity = digest({'recipe': 4, 'source_sha256': sha256(plan['music']['path']),
                       'music': plan['music'], 'duration': plan.get('duration_seconds',30),
                       'last_line_end': plan['narration'][-1]['end']})
    folder = root/'music'/identity
    folder.mkdir(parents=True,exist_ok=True)
    audio, receipt = folder/'music.wav', folder/'receipt.json'
    pending = folder/'pending_receipt.json'
    with (folder/'write.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        cached, selection, audio_sha = _cached_music(plan, audio, receipt, pending, identity, sha256, _valid_duration)
        if not cached:
            selection, audio_sha = _publish_music_cache(plan, folder, audio, receipt, pending, identity, uuid, sha256, write_json)
        _publish_music_output(dest, audio, selection, audio_sha, cached, identity, shutil, uuid, sha256, write_json)


def _render_music(plan, dest):
    from .music_window import select
    from .core.io import write_json
    end=plan['narration'][-1]['end'];length=plan.get('duration_seconds',30)
    start_fade=max(end+.10,length-2.2)
    offset,span=select(plan['music']['path'],plan['music'].get('offset_seconds',0),length)
    cross=min(1.0,span/4)
    count=max(1,math.ceil((length-span)/(span-cross))+1)
    import numpy as np
    import tempfile,wave
    raw=run('ffmpeg','-v','error','-i',plan['music']['path'],'-af',f'atrim=start={offset}:duration={span},asetpts=PTS-STARTPTS','-ac','2','-ar','48000','-f','f32le','pipe:1')
    piece=np.frombuffer(raw,dtype='<f4').reshape(-1,2)
    total=round(length*48000);join=min(round(cross*48000),len(piece)//4)
    if len(piece)<2:raise ValueError('empty music span')
    joined=np.empty((total+len(piece),2),dtype=np.float32)
    joined[:len(piece)]=piece;position=len(piece)
    ramp=np.linspace(0,1,join,dtype=np.float32)[:,None]
    while position<total:
        joined[position-join:position]=joined[position-join:position]*(1-ramp)+piece[:join]*ramp
        joined[position:position+len(piece)-join]=piece[join:]
        position+=len(piece)-join
    with tempfile.TemporaryDirectory(dir=Path(dest).parent) as tmp:
        joined_path=Path(tmp)/'continuous.wav'
        with wave.open(str(joined_path),'wb') as f:
            f.setnchannels(2);f.setsampwidth(2);f.setframerate(48000)
            f.writeframes((np.clip(joined[:total],-1,1)*32767).astype('<i2').tobytes())
        filters=f'loudnorm=I=-26:TP=-7:LRA=8,aecho=0.8:0.8:71|113:0.17|0.11,atrim=duration={length},afade=t=in:d=0.18,afade=t=out:st={start_fade}:d={length-start_fade}'
        ff('-i',joined_path,'-af',filters,'-t',length,'-ar','48000','-ac','2',dest)
    write_json(Path(dest).with_suffix('.selection.json'),{'offset_seconds':offset,'span_seconds':span,'loop_count':count,'crossfade_seconds':cross if count>1 else 0,'fade_start':start_fade,'source':plan['music']['path'],'recipe':'continuous_span_crossfade_v4'})


def audio_effects(plan):
    """Clean voice/music by default; never mix unreviewed generated source audio."""
    from .core.io import sha256
    if plan.get('audio_policy',{}).get('allow_curated_sfx') is not True:
        return []
    accepted=[]
    for effect in plan.get('sound_effects',[]):
        if effect.get('origin')!='curated_sfx' or effect.get('speech_free_verified') is not True:
            continue
        expected=effect.get('source_sha256')
        if not expected or sha256(effect['path'])!=expected:
            raise ValueError('curated sound effect differs from its verified speech-free asset')
        accepted.append(effect)
    return accepted


def clean_audio_plan(plan):
    return {**plan,'use_native_foley':False,'sound_effects':audio_effects(plan),
            'audio_policy':{**plan.get('audio_policy',{}),'mode':'independent_voice_continuous_music',
                            'source_audio_enabled':False}}


def compose(plan, shots, voice, bed, folder):
    folder = Path(folder)
    plan=clean_audio_plan(plan)
    from .core.io import write_json,sha256
    write_json(folder/'audio_mix.json',{'policy':plan['audio_policy'],
        'voice':{'path':str(voice),'sha256':sha256(voice)},
        'music':{'path':str(bed),'sha256':sha256(bed)},
        'curated_effects':plan['sound_effects'],'source_audio_in_mix':False})
    for i, src in enumerate(shots):
        # Generated filenames avoid concat/filter quoting vulnerabilities.
        import shutil
        shutil.copyfile(src, folder / f'shot_{i:02}.mp4')
    (folder/'concat.txt').write_text(''.join(f"file 'shot_{i:02}.mp4'\n" for i in range(len(shots))), encoding="utf-8")
    captions(plan, folder/'captions.ass')
    from .color_policy import filters as color_filters
    grade, color_receipt = color_filters(plan)
    write_json(folder/'color_grading.json',{'policy':plan.get('color_policy'),'shots':color_receipt,'applied_before_captions':True})
    vf = (grade+',' if grade else '') + "ass=captions.ass"
    audio_inputs = ['-i', voice, '-i', bed]
    chains = []
    labels = ['[1:a]', '[2:a]']
    for i, effect in enumerate(plan.get('sound_effects', []), 3):
        audio_inputs += ['-i', effect['path']]
        chains.append(f"[{i}:a]atrim=start={effect.get('offset_seconds',0)}:duration={effect.get('duration_seconds',30)},asetpts=PTS-STARTPTS,volume={effect.get('gain_db',-6)}dB,adelay={round(effect.get('start',0)*1000)}:all=1[fx{i}]")
        labels.append(f'[fx{i}]')
    chains.append(''.join(labels)+f'amix=inputs={len(labels)}:duration=longest:normalize=0,alimiter=limit=0.891:level=0[a]')
    if plan.get('locked_audio'):
        locked = verify_locked_audio(plan)
        ff('-f','concat','-safe','0','-i','concat.txt','-i',locked['path'],
           '-vf',vf,'-map','0:v:0','-map','1:a:0','-t',str(plan['duration_seconds']),'-r','24',
           '-c:v','libx264','-threads','4','-preset','veryfast','-crf','19','-pix_fmt','yuv420p',
           '-c:a','copy','-movflags','+faststart','video.mp4',cwd=folder)
        write_json(folder/'audio_mix.json',{'policy':'locked_approved_audio','source':locked,
                    'audio_stream_copied':True,'source_audio_in_mix':False,'curated_effects':[]})
    else:
        ff('-f','concat','-safe','0','-i','concat.txt',*audio_inputs,
           '-filter_complex',';'.join(chains),
           '-vf',vf,'-map','0:v:0','-map','[a]','-t',str(plan.get('duration_seconds',30)),'-r','24','-c:v','libx264','-threads','4',
           '-preset','veryfast','-crf','19','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',
           '-movflags','+faststart','video.mp4',cwd=folder)
    # Cover takes a hero frame from the last shot and uses independently authored typography.
    ff('-ss','1','-i',shots[-1],'-frames:v','1','hero.png',cwd=folder)
    captions(plan, folder/'cover.ass', cover=True)
    ff('-i','hero.png','-vf','ass=cover.ass','-frames:v','1','cover.png',cwd=folder)
    review_contact(folder/'video.mp4', folder/'contact.jpg')
    return {'video_path':str(folder/'video.mp4'),'cover_path':str(folder/'cover.png'),
            'contact_path':str(folder/'contact.jpg'),'caption_path':str(folder/'captions.ass'),
            'audio_mix_path':str(folder/'audio_mix.json')}


def verify_locked_audio(plan):
    from .core.io import sha256
    locked = plan['locked_audio']
    if sha256(locked['path']) != locked.get('sha256'):
        raise ValueError('locked approved audio changed')
    audio = [s for s in probe(locked['path'])['streams'] if s['codec_type'] == 'audio']
    if not audio or audio[0].get('codec_name') != 'aac':
        raise ValueError('locked audio requires an AAC stream for exact MP4 copying')
    if abs(float(audio[0].get('duration', 0)) - plan['duration_seconds']) > .1:
        raise ValueError('locked approved audio duration differs from story timeline')
    return locked


def review_contact(video, dest, *, plan=None):
    # Visible gutters and timestamps prevent separate samples being read as one scene.
    duration = float(probe(video)['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('invalid review video duration')
    if plan is not None:
        from .caption_timing import events
        import tempfile
        times = {(e['start'] + e['end']) / 2 for e in events(plan)}
        cursor = 0
        for shot in plan['shots']:
            length = shot['duration_seconds']
            times.update((cursor + min(.1, length / 2), cursor + length / 2,
                          cursor + length - min(.1, length / 2)))
            cursor += length
        times = sorted({round(min(duration - .02, max(0, t)), 4) for t in times})
        with tempfile.TemporaryDirectory() as directory:
            for i, t in enumerate(times):
                ff('-ss', str(t), '-i', video, '-vf',
                   f"scale=216:384,drawtext=text='{t:.3f}s':x=4:y=356:fontsize=15:fontcolor=white:box=1:boxcolor=black",
                   '-frames:v', '1', Path(directory) / f'{i:03d}.png')
            ff('-framerate', '1', '-i', str(Path(directory) / '%03d.png'), '-vf',
               f'tile=5x{math.ceil(len(times)/5)}:padding=10:margin=10:color=white',
               '-frames:v', '1', dest)
        Path(str(dest) + '.samples.json').write_text(json.dumps({'times': times,
            'coverage': 'every caption midpoint and each shot start/middle/end'}, ensure_ascii=False), encoding='utf-8')
        return
    # Fifteen samples span the entire film, including long-form closing beats.
    interval = max(duration - min(.1, duration / 15), duration / 2) / 14
    ff('-i', video, '-vf', f"select='gte(t,selected_n*{interval:.8f})',scale=216:384," +
       r"drawtext=text='%{pts\:hms}':x=4:y=356:fontsize=15:fontcolor=white:box=1:boxcolor=black," +
       'tile=5x3:padding=10:margin=10:color=white', '-frames:v', '1', dest)


def video_duration_matches(stream, expected, tolerance=.1):
    """Check video independently; a longer audio stream can hide truncated video."""
    try:
        duration=float(stream['duration'])
        return math.isfinite(duration) and abs(duration-expected)<tolerance
    except (KeyError,TypeError,ValueError):
        return False


def quality(plan, video, bed):
    info = probe(video)
    v = next(s for s in info['streams'] if s['codec_type']=='video')
    checks = {'duration':abs(float(info['format']['duration'])-plan.get('duration_seconds',30))<.10,
              'video_duration':video_duration_matches(v,plan.get('duration_seconds',30)),
              'vertical_1080p':(v['width'],v['height'])==(1080,1920),
              'has_audio':any(s['codec_type']=='audio' for s in info['streams'])}
    run('ffmpeg','-v','error','-i',video,'-f','null','-')
    checks['full_decode'] = True
    end = plan['narration'][-1]['end']
    levels = [{'start':round(a,2),'dbfs':audio_rms(bed,a,.25)} for a in (end-.8,end-.4)]
    checks['closing_music_present'] = all(x['dbfs']>-40 for x in levels)
    checks['tail_reserved'] = end<=plan.get('duration_seconds',30)-.9
    return {'passed':all(checks.values()),'checks':checks,'closing_music_windows':levels,
            'editorial_status':'review_required',
            'unverified':['semantic source fidelity','bridge/structure geometry across shots','voice pronunciation and aesthetic quality'],
            'note':'Technical pass does not imply visual/editorial approval. No auto-publishing.'}
