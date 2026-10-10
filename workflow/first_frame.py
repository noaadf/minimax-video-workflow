"""Opt-in first-frame video consumption; never waives story or final audio review."""
import hashlib
import json
import math
from pathlib import Path
from .code_identity import code_identity
import re
import subprocess

from .core.io import sha256, write_json

STRATEGY = 'first_frame_only_visual_only_v1'
CONTRACT = {'conditioning': 'first_frame_only', 'output': 'visual_only',
            'end_state_validation': 'generated_video_first_middle_last'}
VERSION = 'first-frame-visual-consumer-v1'
CODE_SHA256 = code_identity(__file__)


def require_runtime(receipt, requirements):
    from .runner import RUNNER_CODE_SHA256
    expected = {'visual_consumer_version': VERSION, 'loaded_visual_consumer_sha256': CODE_SHA256,
                'loaded_runner_sha256': RUNNER_CODE_SHA256}
    if requirements != expected or any(receipt.get(k) != v for k, v in expected.items()):
        raise ValueError('first-frame visual_only consumer is not loaded by the active renderer')
    return expected


def runtime_requirements():
    from .runner import RUNNER_CODE_SHA256
    return {'visual_consumer_version': VERSION, 'loaded_visual_consumer_sha256': CODE_SHA256,
            'loaded_runner_sha256': RUNNER_CODE_SHA256}


def enabled(plan):
    return plan.get('image_generation_strategy') == STRATEGY


def _validate_shot_policy(plan):
    for s in plan.get('shots', []):
        if s.get('use_native_foley') is True or s.get('foley_source_path'):
            raise ValueError('visual_only does not consume native shot audio')
        if s.get('source_path') or s.get('production_mode') == 'deterministic_graphic':
            continue
        if s.get('generation_contract') != CONTRACT:
            raise ValueError('each generated shot requires the exact first_frame_only visual_only contract')
        if not s.get('keyframe_prompts', {}).get('start'):
            raise ValueError('first-frame strategy requires an authored start-image prompt')
        if len(s.get('reference_images', [])) > 1 or s.get('keyframe_prompts', {}).get('end'):
            raise ValueError('first_frame_only cannot carry an end-image request or reference')


def _require_probe_execution(documents, cfg, profile):
    report, visual, adapter = (documents[k] for k in ('probe_report', 'visual_evidence', 'adapter_evidence'))
    if (report.get('reference_count') != 1 or report.get('condition_frame_indices') != [0]
            or report.get('server_result', {}).get('status') != 'completed'
            or visual.get('technical_status') != 'passed'
            or visual.get('single_first_frame_execution') != 'proved_on_this_slot'
            or report.get('raw_sha256') != adapter.get('video_sha256')
            or report.get('resource', {}).get('capability') != cfg.get('capability')):
        raise ValueError('probe does not prove single-first-frame technical execution')
    return profile


def _require_completed_request(persisted, request, state, checksum, server, result):
    if (persisted != request or state.get('task_id') != request['task_id']
            or state.get('request_sha256') != checksum or state.get('submission_ambiguous') is not False
            or state.get('server_job_id') != server.get('id') or server.get('status') != 'completed'
            or result.get('task_id') != request['task_id']):
        raise ValueError('raw video lacks an unambiguous completed exact-request binding')


def _require_dimensions(info, videos, request):
    width, height = videos[0]['width'], videos[0]['height']
    length = float(info['format']['duration'])
    if not math.isfinite(length) or abs(length-request['duration_seconds']) > max(.5,request['duration_seconds']*.125):
        raise ValueError('visual_only video duration mismatch')
    if width <= 0 or height <= 0 or abs(height/width-16/9)/(16/9) > .15:
        raise ValueError('visual_only video orientation mismatch')
    return width, height, length


def validate_policy(plan):
    opted = enabled(plan)
    contracts = [s.get('generation_contract') for s in plan.get('shots', [])
                 if not s.get('source_path') and s.get('production_mode') != 'deterministic_graphic']
    if not opted:
        if any(c is not None for c in contracts):
            raise ValueError('generation_contract requires a new explicit first-frame manifest strategy')
        return
    if plan.get('pacing_policy') != 'storyboard_v1' or plan.get('review_mode', 'api') != 'api':
        raise ValueError('first-frame strategy requires storyboard and automatic story/visual review')
    if plan.get('use_native_foley') is not False or plan.get('audio_policy', {}).get('source_audio_enabled') is not False:
        raise ValueError('visual_only must explicitly disable native foley and source audio')
    if not isinstance(plan.get('shared_visual_spec'), dict) or not plan['shared_visual_spec']:
        raise ValueError('first-frame strategy requires authored shared_visual_spec for whole-board identity review')
    _validate_shot_policy(plan)

def reference_roles(plan, shot):
    validate_policy(plan)
    if enabled(plan):
        return ['start']
    return ['start', 'end'] if shot['storyboard']['state_before'] != shot['storyboard']['state_after'] else ['start']


def require_profile(cfg):
    """An explicit local capability profile binds this exact slot to real evidence."""
    binding = cfg.get('first_frame_visual_only_validation', {})
    if cfg.get('required_visual_consumer_version') != VERSION:
        raise ValueError('first-frame profile requires the explicit consumer version')
    path = Path(binding.get('path', ''))
    if not path.is_file() or sha256(path) != binding.get('sha256'):
        raise ValueError('first-frame visual_only capability has no unchanged probe profile')
    profile = json.loads(path.read_text(encoding="utf-8"))
    for key in ('resource_id', 'capability', 'api_port', 'logical_device_ids'):
        if profile.get(key) != cfg.get(key):
            raise ValueError('first-frame probe profile belongs to a different service: ' + key)
    if profile.get('conditioning') != 'first_frame_only' or profile.get('output') != 'visual_only':
        raise ValueError('wrong first-frame probe profile contract')
    documents = {}
    for key in ('probe_report', 'visual_evidence', 'adapter_evidence'):
        item = profile[key]
        if sha256(item['path']) != item['sha256']:
            raise ValueError('changed first-frame probe evidence: ' + key)
        documents[key] = json.loads(Path(item['path']).read_text(encoding="utf-8"))
    return _require_probe_execution(documents, cfg, profile)

def worker_digest(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def silent_failure(result):
    error = result.get('error', {})
    match = re.fullmatch(r'audio_silent_or_undecodable: max_volume=(-?\d+(?:\.\d+)?) dB', error.get('message', ''))
    return (result.get('status') == 'failed' and error.get('code') == 'INVALID_OUTPUT'
            and error.get('retryable') is False and match is not None
            and float(match[1]) <= -55.0)


def remote_evidence_script(request, cfg, *, failed):
    """Read-only reconciliation: no HTTP POST and no worker retry."""
    directory = request['output_dir'] + '/' + request['task_id'] + '-' + worker_digest(request)[:16]
    raw = directory + ('/video.mp4.part' if failed else '/video.mp4')
    return '''import pathlib,json,hashlib,urllib.request
directory=pathlib.Path(%r); raw=pathlib.Path(%r)
state=json.loads((directory/'state.json').read_text(encoding="utf-8"))
request=json.loads((directory/'request.json').read_text(encoding="utf-8"))
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
server=json.load(opener.open(%r+'/v1/videos/'+state['server_job_id'],timeout=30))
def checksum(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
 return h.hexdigest()
server_path=pathlib.Path(server['file_path'])
assert server_path.resolve().is_relative_to(pathlib.Path(request['output_dir']).resolve()), 'server output escaped request root'
assert checksum(raw)==checksum(server_path), 'worker raw differs from server output'
print(json.dumps(dict(state=state,request=request,server=server,raw_path=str(raw),raw_sha256=checksum(raw),worker_sha256=checksum(%r),reference_sha256=[checksum(p) for p in request['reference_images']])) )
''' % (directory, raw, 'http://127.0.0.1:' + str(int(cfg['api_port'])), cfg['worker_script'])


def _verify_request_evidence(raw, request, result, evidence, profile, expected_reference_sha256):
    checksum = worker_digest(request)
    state, persisted, server = evidence['state'], evidence['request'], evidence['server']
    _require_completed_request(persisted, request, state, checksum, server, result)
    if evidence.get('worker_sha256') != profile['worker_sha256']:
        raise ValueError('unverified remote worker implementation; cannot interpret failure ordering')
    if sha256(raw) != evidence.get('raw_sha256'):
        raise ValueError('raw video changed during transfer')
    if len(request.get('reference_images', [])) != 1:
        raise ValueError('first-frame consumer requires exactly one actual reference')
    if len(expected_reference_sha256) != 1 or evidence.get('reference_sha256') != expected_reference_sha256:
        raise ValueError('remote first image differs from the reviewed local reference')
    return checksum, server


def _verify_streams(raw, request):
    from . import media
    info = media.probe(raw)
    videos = [s for s in info['streams'] if s['codec_type'] == 'video']
    audios = [s for s in info['streams'] if s['codec_type'] == 'audio']
    if len(videos) != 1 or not audios:
        raise ValueError('expected one video and native audio stream; missing audio is not silence')
    return _require_dimensions(info, videos, request)


def _verify_native_audio(raw, recovering):
    decode = subprocess.run(['ffmpeg','-v','error','-i',str(raw),'-f','null','-'],capture_output=True,text=True,timeout=600)
    if decode.returncode or decode.stderr.strip() or decode.stdout.strip():
        raise ValueError('visual_only full-stream decode failed')
    levels = subprocess.run(['ffmpeg','-i',str(raw),'-vn','-af','volumedetect','-f','null','-'],capture_output=True,text=True,timeout=600)
    match = re.search(r'max_volume:\s*(-?\d+(?:\.\d+)?) dB',levels.stderr)
    if levels.returncode or not match or not math.isfinite(float(match[1])):
        raise ValueError('native audio measurement failed; cannot treat undecodable audio as silence')
    peak = float(match[1])
    if recovering and peak > -55:
        raise ValueError('worker audio failure not reproduced locally')
    return peak


def verify_and_strip(raw, destination, request, result, evidence, cfg, *, expected_reference_sha256):
    """Accept video bytes only after independent checks; preserve failed worker receipt.

    An 'undecodable' error string alone proves nothing. Decode all streams, require
    a real audio stream, and independently remeasure silence before considering it
    a recoverable audio-only failure. No generic worker threshold is changed.
    """
    from . import media
    profile = require_profile(cfg)
    recovering = result.get('status') != 'succeeded'
    if recovering and not silent_failure(result):
        raise ValueError('not an exclusively eligible native-audio failure')
    checksum, server = _verify_request_evidence(raw, request, result, evidence, profile, expected_reference_sha256)
    width, height, length = _verify_streams(raw, request)
    peak = _verify_native_audio(raw, recovering)
    media.ff('-i',raw,'-map','0:v:0','-c:v','copy','-an',destination)
    if any(s['codec_type'] == 'audio' for s in media.probe(destination)['streams']):
        raise ValueError('visual_only derivative still contains native audio')
    receipt = {'schema_version':1,'consumer_version':VERSION,'status':'visual_only_technical_passed',
               'content_approved':False,'native_audio_consumed':False,'worker_status':result['status'],
               'worker_failure_preserved':result.get('error'),'request_sha256':checksum,
               'server_job_id':server['id'],'raw_sha256':sha256(raw),'video_sha256':sha256(destination),
               'duration_seconds':length,'width':width,'height':height,'native_audio_max_db':peak,
               'full_decode_passed':True,'remaining_gates':['whole_film_visual','shot_action_and_end_state',
               'independent_narration_music','speech','closing_audio'],'capability_profile_sha256':cfg['first_frame_visual_only_validation']['sha256']}
    write_json(Path(destination).parent/'visual_only_receipt.json',receipt)
    return receipt
