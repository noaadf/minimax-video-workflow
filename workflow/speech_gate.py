"""SHA-bound speech screening; one bounded ASR process, no agent or publishing calls."""
import copy
import fcntl
import hashlib
import json
import os
import signal
import subprocess
from pathlib import Path

from .core.io import digest, sha256, write_json
from .speech_qc import classify_phonetics
from .review_state import mark_review_required

VERSION = 'speech-gate-v1'
ROOT = Path(__file__).resolve().parents[1]


def expected_text(plan):
    return ''.join(row['text'] for row in plan['narration'])


def validate_receipt(report, video_hash, expected, model, model_files):
    if not isinstance(report, dict) or not isinstance(report.get('binding'), dict):
        raise ValueError('Malformed speech receipt; report and binding objects required')
    binding = report['binding']
    if not isinstance(binding.get('settings'), dict):
        raise ValueError('Malformed speech receipt; settings object required')
    settings = binding['settings']
    if binding['video_sha256'] != video_hash or binding['expected_text'] != expected:
        raise ValueError('receipt belongs to different media or narration')
    if report['expected'] != expected or report.get('audio_source') != 'video':
        raise ValueError('final-mix receipt required')
    if settings.get('model') != model or settings.get('model_files') != model_files or not model_files:
        raise ValueError('unrecognized or unbound ASR model')
    if report.get('recognizer_prompted_with_expected_text') is not False or settings.get('initial_prompt') is not None:
        raise ValueError('unprompted recognition required')
    if not isinstance(report.get('recognized'), str) or not isinstance(report.get('segments'), list):
        raise ValueError('missing raw transcript')
    if ''.join(row['text'] for row in report['segments']) != report['recognized']:
        raise ValueError('raw segments and transcript disagree')
    return report



def _receipt_entries(index, require_items=False):
    """Validate index shape before using it as recovery evidence."""
    value = json.loads(index.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get('items'), list):
        raise ValueError('Malformed speech receipt index; items list required')
    items = value['items']
    if require_items and not items:
        raise ValueError('Speech receipt index is empty; diagnose before retry')
    for entry in items:
        if not isinstance(entry, dict) or not isinstance(entry.get('binding', {}), dict):
            raise ValueError('Malformed speech receipt index entry or binding')
    return items


def route(report, receipt_path, cached, receipt_hash):
    comparison = classify_phonetics(report['expected'], report['recognized'])
    differences = comparison['differences']
    uncertain = [d for d in differences if d['classification'] != 'homophone_spelling']
    status = 'phonetic_review_required' if uncertain else ('homophone_spelling_only' if differences else 'transcript_match')
    # Empty transcripts are never a successful speech check, even for an empty plan.
    if not comparison['recognized_normalized']:
        status = 'missing_transcript'
    return {'version': VERSION, 'status': status,
            'review_required': bool(uncertain) or status == 'missing_transcript',
            'content_approved': False, 'comparison': comparison,
            'binding': report['binding'], 'receipt_path': str(receipt_path),
            'receipt_sha256': receipt_hash, 'cache_hit': cached,
            'api_calls': 0, 'agent_review_calls': 0,
            'limits': ['ASR and dictionary pinyin only; no pronunciation or performance certification.',
                       'Does not approve music, emotional closing, facts, geometry or the complete film.']}



def _screen_receipt(receipt_path, video_hash, expected, cfg, cached):
    # Bind the decision to the exact bytes parsed, not a later version of the path.
    raw = receipt_path.read_bytes()
    receipt_hash = hashlib.sha256(raw).hexdigest()
    report = validate_receipt(json.loads(raw), video_hash, expected, cfg['model_id'], cfg['model_files'])
    result = route(report, receipt_path, cached, receipt_hash)
    if sha256(receipt_path) != receipt_hash:
        raise ValueError('Speech receipt changed while screening; evidence requires review')
    return result


def _bounded_worker(command, log_path, timeout):
    with open(log_path, 'wb') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise RuntimeError('ASR worker timed out; process group stopped, no automatic retry')
    if code:
        raise RuntimeError(f'ASR worker exited {code}; inspect {log_path}')


def screen(video, plan, folder, config_path=None):
    """Use verified receipts first. A cache miss runs at most one configured CPU worker."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    config_path = Path(config_path or os.environ.get('WORKFLOW_SPEECH_CONFIG', ROOT/'research/speech_runtime.json'))
    def finish(value):
        write_json(folder/'report.json', value)
        return value
    def unavailable(reason, status='speech_not_assessed'):
        return finish({'version': VERSION, 'status': status, 'reason': reason,
                       'review_required': True, 'content_approved': False, 'api_calls': 0, 'agent_review_calls': 0})
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        video = Path(video).resolve(); video_hash = sha256(video); expected = expected_text(plan)
        cache_root = Path(cfg['cache_root']); cache_root.mkdir(parents=True, exist_ok=True)
        key = digest({'video_sha256': video_hash, 'expected': expected, 'model': cfg['model_id'], 'model_files': cfg['model_files']})
        cache = cache_root/key
        indices = [cache/'index.json'] + [Path(p) for p in cfg.get('receipt_indices', [])]
        for index in indices:
            if not index.is_file():
                continue
            for entry in _receipt_entries(index):
                if entry.get('binding', {}).get('video_sha256') != video_hash:
                    continue
                if entry['binding'].get('expected_text') != expected:
                    continue
                receipt_path = Path(entry['report_path'])
                return finish(_screen_receipt(receipt_path, video_hash, expected, cfg, True))
        if not cfg.get('allow_new_transcription', False):
            return unavailable('No matching receipt; new recognition disabled')
        python = Path(cfg['python']); model = Path(cfg['model_path'])
        if not python.is_file() or not (model/'model.bin').is_file():
            return unavailable('Configured ASR runtime or local model unavailable; no downloads attempted')
        # Across concurrent quality workers, never load a second copy of the CPU model.
        with open(cache_root/'worker.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return unavailable('ASR worker busy; deferred for later speech screening, no duplicate process', status='speech_deferred')
            # Recheck after acquiring the lock: a concurrent worker may just have finished.
            if (cache/'index.json').is_file():
                entry = _receipt_entries(cache/'index.json', require_items=True)[0]
                receipt_path = Path(entry['report_path'])
                return finish(_screen_receipt(receipt_path, video_hash, expected, cfg, True))
            if (cache/'attempt.json').is_file():
                return unavailable('Previous ASR attempt incomplete or failed; diagnose before retry')
            cache.mkdir(parents=True, exist_ok=True)
            for filename, expected_hash in cfg['model_files'].items():
                if sha256(model/filename) != expected_hash:
                    return unavailable('ASR model fingerprint changed; configuration requires review')
            write_json(cache/'attempt.json', {'state':'started', 'automatic_retry':False})
            manifest = cache/'manifest.json'; write_json(manifest, plan)
            request = {'items': [{'id': plan.get('id', key), 'name': 'final_mix', 'assets': {
                'video': {'path': str(video), 'sha256': video_hash},
                'manifest': {'path': str(manifest), 'sha256': sha256(manifest)}}}]}
            write_json(cache/'request.json', request)
            worker_error = None
            try:
                _bounded_worker([str(python), str(ROOT/'scripts/audit_speech_batch.py'),
                                 '--config', str(cache/'request.json'), '--output', str(cache), '--model', str(model)],
                                cache/'worker.log', min(600, max(1, int(cfg.get('timeout_seconds', 180)))))
            except RuntimeError as error:
                # Some native runtimes abort at shutdown after committing the receipt.
                # Recovery still requires the complete SHA/model/unprompted validation below.
                if not (cache/'index.json').is_file():
                    raise
                worker_error = str(error)
            report = _receipt_entries(cache/'index.json', require_items=True)[0]
            receipt_path = Path(report['report_path'])
            result = _screen_receipt(receipt_path, video_hash, expected, cfg, False)
            if worker_error:
                result['worker_exit_warning'] = worker_error
                result['recovered_validated_receipt'] = True
            return finish(result)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        return unavailable(f'{type(error).__name__}: {error}')


def retain_review(editorial, screening):
    """A visual pass cannot erase missing speech evidence or phonetic uncertainty."""
    editorial = copy.deepcopy(editorial)
    if screening.get('review_required', True):
        review = mark_review_required(editorial)
        review.setdefault('findings', []).append({'severity': 'medium', 'category': 'speech',
            'issue': screening.get('reason', screening['status']), 'shot_ids': [],
            'evidence': screening.get('receipt_path'), 'confirmed_voice_error': False})
    return editorial
