"""Reuse dense, version-bound shot evidence for deterministic postproduction only."""
import copy
import json
from pathlib import Path
from .core.io import sha256
from .narrative_gate import AUTHORED_FIELDS
from .scoped_action_evidence import validate_dense_review
from .delivery_policy import valid_review, is_blocking


def read_bound(link):
    path = Path(link['path'])
    if sha256(path) != link['sha256']:
        raise ValueError('Dense delivery evidence changed')
    return json.loads(path.read_text(encoding="utf-8"))


def authored(plan):
    result = copy.deepcopy({k: plan.get(k) for k in AUTHORED_FIELDS})
    for shot in result['shots']:
        shot['reference_images'] = shot.get('reference_images') or []
    return result


def validate(record, plan, video):
    if record.get('kind') != 'dense_postproduction_v1':
        raise ValueError('Unknown dense audit type')
    candidate_sha = sha256(video)
    if candidate_sha != record['candidate_sha256']:
        raise ValueError('Candidate changed after review')
    source_plan = read_bound(record['source_manifest'])
    if authored(plan) != authored(source_plan):
        raise ValueError('Authored content changed after dense review')
    source_video = record['source_video']
    if sha256(source_video['path']) != source_video['sha256']:
        raise ValueError('Original reviewed video changed')
    read_bound(record['quality'])
    read_bound(record['supplement'])
    dense = validate_dense_review(record['quality']['path'], source_plan,
                                  source_video['path'], record['supplement']['path'])
    editorial = read_bound(record['editorial'])
    result = editorial['result']
    review = result.get('review')
    if (editorial.get('video_sha256') != candidate_sha or not result.get('receipt')
            or result.get('status') != 'automatic_review_passed' or not valid_review(review)
            or review['verdict'] != 'pass' or any(is_blocking(f) for f in review['findings'])):
        raise ValueError('Candidate editorial review incomplete')
    bundle = read_bound(record['candidate_checks'])
    qc = bundle['temporal_qc']
    if (bundle['candidate_sha256'] != candidate_sha or bundle['source_video_sha256'] != source_video['sha256']
            or qc['binding']['video_sha256'] != candidate_sha or qc.get('technical_scan_passed') is not True
            or qc.get('exceptions') or bundle.get('decoded_audio_identical') is not True):
        raise ValueError('Candidate technical evidence incomplete')
    return dense
