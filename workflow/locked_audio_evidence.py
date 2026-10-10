"""Revalidate copied audio against its independent-mix provenance, not its label."""
import json
import subprocess
from pathlib import Path
from .core.io import sha256


def decoded_hash(path):
    result = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
                             '-f', 'hash', '-hash', 'sha256', '-'],
                            check=True, capture_output=True, text=True, timeout=120)
    value = result.stdout.strip()
    if not value.startswith('SHA256=') or len(value) != 71:
        raise ValueError('Invalid decoded audio digest')
    return value


def validate(mix_path, report_path, candidate, plan):
    mix = json.loads(Path(mix_path).read_text(encoding="utf-8"))
    report_path = Path(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (mix.get('policy') != 'locked_approved_audio' or mix.get('source_audio_in_mix') is not False
            or mix.get('curated_effects') != [] or mix.get('audio_stream_copied') is not True):
        raise ValueError('Copied clean audio policy not evidenced')
    source = Path(mix['source']['path'])
    if sha256(source) != mix['source']['sha256'] or sha256(source) != report.get('preview_sha256'):
        raise ValueError('Locked audio source changed')
    if Path(report['preview']).resolve() != source.resolve() or report.get('source_audio_in_mix') is not False:
        raise ValueError('Independent mix source mismatch')
    edl_path = report_path.with_name('edl.json')
    if sha256(edl_path) != report['edl_sha256']:
        raise ValueError('Mix recipe changed')
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    inputs = report['input_sha256']
    for key in ('narration_path', 'music_path'):
        path = edl[key]
        if path not in inputs or sha256(path) != inputs[path]:
            raise ValueError('Missing or changed independent audio stem')
    for path, checksum in inputs.items():
        if sha256(path) != checksum:
            raise ValueError('Mix input changed')
    if edl['caption_plan']['narration'] != plan['narration']:
        raise ValueError('Narration differs from copied mix')
    source_hash = decoded_hash(source)
    if decoded_hash(candidate) != source_hash:
        raise ValueError('Candidate audio differs from verified mix')
    return {'status': 'locked_audio_chain_verified', 'candidate_sha256': sha256(candidate),
            'source_sha256': sha256(source), 'decoded_audio_sha256': source_hash,
            'report_sha256': sha256(report_path), 'edl_sha256': sha256(edl_path),
            'user_approved': False, 'listening_verified': False}
