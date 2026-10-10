"""CPU media inspection and local ASR, with explicit unreviewed status."""
import json
import re
import subprocess
from pathlib import Path
from .core import sha256, write_json


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format',
                                              '-show_streams', '-of', 'json', str(path)]))


def index_video(path, output, root, asset_id='A1', threshold=.22):
    path, output, root = Path(path), Path(output), Path(root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    info = probe(path)
    duration = float(info['format']['duration'])
    scan = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(path), '-an', '-vf',
                           f"scale=320:-2,select='gt(scene,{threshold})',showinfo", '-fps_mode',
                           'vfr', '-f', 'null', '-'], capture_output=True, text=True, check=True)
    cuts = sorted(set(float(x) for x in re.findall(r'pts_time:([0-9.]+)', scan.stderr)))
    boundaries = [0.] + [x for x in cuts if 0 < x < duration] + [duration]
    shots = []
    for i, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1):
        at = (start + end) / 2
        frame = output / f'S{i:03d}.jpg'
        subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(path), '-frames:v',
                        '1', '-vf', 'scale=640:-2', '-y', str(frame)], check=True)
        shots.append({'id': f'S{i:03d}', 'asset_id': asset_id, 'start': start, 'end': end,
                      'status': 'candidate', 'observation': None,
                      'keyframes': [{'at': at, 'path': str(frame.resolve().relative_to(root)),
                                     'sha256': sha256(frame)}],
                      'boundary_method': f'ffmpeg scene score > {threshold}; cuts need visual review'})
    write_json(output / 'shots.json', shots)
    write_json(output / 'probe.json', info)
    return shots


def transcribe(path, output, model_path, threads=2):
    # Optional existing environment only; no downloads or remote inference.
    import numpy as np
    from faster_whisper import WhisperModel
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-vn',
                                   '-ar', '16000', '-ac', '1', '-f', 'f32le', '-'])
    samples = np.frombuffer(raw, dtype=np.float32).copy()
    if not len(samples):
        raise ValueError('no decoded audio')
    model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=threads)
    segments, info = model.transcribe(samples, language='zh', beam_size=3, word_timestamps=True,
                                      vad_filter=True, condition_on_previous_text=False)
    result = {'status': 'automatic_unreviewed', 'method': 'faster-whisper-small CPU int8, zh, beam3, VAD',
              'source_sha256': sha256(path), 'duration': len(samples)/16000,
              'segments': [{'start': s.start, 'end': s.end, 'text': s.text,
                            'words': [{'start': w.start, 'end': w.end, 'text': w.word,
                                       'probability': w.probability} for w in s.words or []]}
                           for s in segments],
              'limitations': ['ASR is unreviewed; names, numerals and dates need source comparison.',
                              'Empty transcript means no recognized speech, not confirmed absence of speech.']}
    write_json(output, result)
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('video'); p.add_argument('output'); p.add_argument('--root', required=True)
    p.add_argument('--model')
    args = p.parse_args()
    shots = index_video(args.video, args.output, args.root)
    print(json.dumps({'shots': len(shots), 'status': 'candidate'}, ensure_ascii=False), flush=True)
    if args.model:
        result = transcribe(args.video, Path(args.output)/'transcript.json', args.model)
        print(json.dumps({'segments': len(result['segments']), 'status': result['status']}), flush=True)
