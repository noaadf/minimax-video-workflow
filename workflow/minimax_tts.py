"""Local MiniMax speech-02 synthesis; word cues come from ASR, not the API.

The Plus key serves image and speech from one quota, api.minimaxi.com is
reachable from the Mac directly, and speech-02 returns no word timestamps,
so caption alignment runs locally via faster-whisper (method asr_estimate).
"""
import fcntl, hashlib, json, math, os, time, urllib.request, uuid
from pathlib import Path

URL = 'https://api.minimaxi.com/v1/t2a_v2'


def _key():
    for name in ('MINIMAX_TTS_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY'):
        value = os.environ.get(name)
        if value:
            return value
    raise RuntimeError('MiniMax speech needs a key in MINIMAX_TTS_API_KEY/ANTHROPIC_AUTH_TOKEN/ANTHROPIC_API_KEY')


def _t2a(text, voice, key):
    payload = {'model': voice.get('model', 'speech-02-hd'), 'text': text, 'stream': False,
               'voice_setting': {'voice_id': voice.get('name', 'presenter_male'),
                                 'speed': voice.get('speed', 1.0), 'vol': voice.get('vol', 1.0),
                                 'pitch': voice.get('pitch', 0)},
               'audio_setting': {'sample_rate': 32000, 'bitrate': 128000, 'format': 'mp3', 'channel': 1}}
    req = urllib.request.Request(URL, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read())
    base = body.get('base_resp') or {}
    if base.get('status_code') not in (0, None):
        raise RuntimeError('minimax t2a error %s: %s' % (base.get('status_code'), base.get('status_msg')))
    hex_audio = (body.get('data') or {}).get('audio')
    if not isinstance(hex_audio, str) or len(hex_audio) < 1024:
        raise RuntimeError('minimax t2a returned no audio')
    data = bytes.fromhex(hex_audio)
    if len(data) < 512:
        raise RuntimeError('minimax t2a audio too short')
    return data


def _asr_words(audio_path):
    from faster_whisper import WhisperModel
    model = WhisperModel('small', device='cpu', compute_type='int8')
    segments, _ = model.transcribe(str(audio_path), language='zh', word_timestamps=True,
                                   vad_filter=True, beam_size=5)
    words = []
    for segment in segments:
        for word in (segment.words or []):
            text = (word.word or '').strip()
            if text and word.start is not None and word.end is not None and word.end > word.start:
                words.append({'start': float(word.start), 'end': float(word.end), 'text': text})
    return words


def _cache_folder(text, voice):
    root = Path(os.environ.get('WORKFLOW_AUDIO_CACHE',
                               str(Path(__file__).resolve().parents[1] / 'output/production/audio_cache')))
    identity = hashlib.sha256(json.dumps({'schema': 1, 'engine': 'minimax', 'text': text, 'voice': voice},
                                         sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    folder = root / 'speech' / identity
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _cached_audio(text, voice, temp_audio):
    """Return cached bytes without paying the API again; otherwise synthesize and store."""
    folder = _cache_folder(text, voice)
    cached, receipt = folder / 'audio.mp3', folder / 'receipt.json'
    with (folder / 'write.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = cached.read_bytes()
            record = json.loads(receipt.read_text(encoding="utf-8"))
            if record.get('sha256') == hashlib.sha256(data).hexdigest():
                temp_audio.write_bytes(data)
                return
        except (OSError, ValueError):
            pass
        data = _t2a(text, voice, _key())
        temp_audio.write_bytes(data)
        part = folder / 'audio.part.mp3'
        part.write_bytes(data)
        os.replace(part, cached)
        temp = folder / 'receipt.part.json'
        temp.write_text(json.dumps({'sha256': hashlib.sha256(data).hexdigest(), 'voice': voice,
                                    'bytes': len(data), 'at': time.time()}, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, receipt)


def synthesize_minimax(text, voice, destination):
    """Plain speech synthesis, no caption timing."""
    audio = Path(destination).resolve()
    if audio.exists():
        raise ValueError('minimax speech needs a new output path')
    audio.parent.mkdir(parents=True, exist_ok=True)
    temp = audio.with_name(uuid.uuid4().hex + '.part.mp3')
    try:
        _cached_audio(text, voice, temp)
        os.replace(temp, audio)
        return {'path': str(audio), 'sha256': hashlib.sha256(audio.read_bytes()).hexdigest()}
    finally:
        temp.unlink(missing_ok=True)


def _per_char_words(text, mapped, borrowed):
    """Rebuild the word list as one entry per spoken script char.

    faster-whisper swallows leading function chars (而/已) into neighbouring
    tokens, which leaves caption boundary chars without a cue (r12 lesson).
    Every char gets the aligned time; ASR-dropped chars borrow their nearest
    neighbour's span. align_script then finds a 1:1 char mapping and every
    caption boundary lands on a real cue."""
    from .speech_timing import spoken_chars
    words = []
    for char, index in spoken_chars(text):
        t = mapped.get(index) or borrowed.get(index)
        if t is None:
            continue
        t0, t1 = t
        if words and t0 < words[-1]['end'] - .001:
            t0 = words[-1]['end']  # keep the ordered-and-non-overlapping invariant
        if t1 <= t0:
            t1 = t0 + .01
        words.append({'start': t0, 'end': t1, 'text': char})
    return words


def _repair_cue_coverage(text, evidence):
    """Ensure every spoken script char is a timing point (asr_estimate only)."""
    from .speech_timing import align_script, spoken_chars
    mapped, _ = align_script(text, evidence, minimum_match=0.5)
    script = [index for _, index in
              spoken_chars(text)]
    ordered = sorted(mapped)
    borrowed = {}
    for index in script:
        if index in mapped:
            continue
        neighbours = [j for j in ordered if abs(j - index) <= 2]
        if neighbours:
            borrowed[index] = mapped[min(neighbours, key=lambda j: abs(j - index))]
    evidence['words'] = _per_char_words(text, mapped, borrowed)
    evidence['cue_repair'] = {'borrowed_chars': sorted(borrowed)}
    return evidence


def synthesize_minimax_timed(text, voice, destination, timing_destination):
    """Speech plus caption evidence; publishes the audio/words pair atomically."""
    from .media import duration
    from .core.io import write_json
    from .speech_timing import align_script
    if not isinstance(text, str) or not 0 < len(text) <= 5000:
        raise ValueError('invalid speech text')
    if not isinstance(voice, dict) or voice.get('engine') != 'minimax':
        raise ValueError('minimax timed speech needs engine voice')
    audio, timing = Path(destination).resolve(), Path(timing_destination).resolve()
    if audio == timing or audio.exists() or timing.exists():
        raise ValueError('minimax timed speech needs new output paths')
    audio.parent.mkdir(parents=True, exist_ok=True)
    timing.parent.mkdir(parents=True, exist_ok=True)
    temp_audio = audio.with_name(uuid.uuid4().hex + '.part.mp3')
    temp_words = timing.with_name(uuid.uuid4().hex + '.part.json')
    publishing = False
    try:
        _cached_audio(text, voice, temp_audio)
        seconds = duration(temp_audio)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('invalid minimax speech duration')
        evidence = {'schema_version': 1, 'method': 'asr_estimate', 'script_text': text,
                    'source_path': str(temp_audio),
                    'source_sha256': hashlib.sha256(temp_audio.read_bytes()).hexdigest(),
                    'source_seconds': seconds, 'words': _asr_words(temp_audio), 'voice': voice,
                    'listening_verified': False,
                    'asr': {'engine': 'faster-whisper', 'model': 'small', 'language': 'zh'}}
        _repair_cue_coverage(text, evidence)
        align_script(text, evidence, minimum_match=0.5)
        evidence['source_path'] = str(audio)
        write_json(temp_words, evidence)
        publishing = True
        os.link(temp_audio, audio)
        os.link(temp_words, timing)
        publishing = False
        return {'path': str(audio), 'sha256': evidence['source_sha256'], 'bytes': audio.stat().st_size,
                'words_path': str(timing),
                'request_id': hashlib.sha256(json.dumps({'text': text, 'voice': voice},
                                                        sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    finally:
        if not publishing:
            temp_audio.unlink(missing_ok=True)
            temp_words.unlink(missing_ok=True)
