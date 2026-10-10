"""Purpose-based planning and hash-bound roughcut review, without queue writes."""
import math


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")


def validate_beats(plan):
    editorial = plan.get("editorial")
    if not isinstance(editorial, dict):
        raise ValueError("editorial_v2 requires editorial")
    _text(editorial.get("central_question"), "central_question")
    beats = editorial.get("beats")
    if not isinstance(beats, list) or not beats:
        raise ValueError("editorial beats must be a nonempty list")
    ids = set()
    for beat in beats:
        if not isinstance(beat, dict):
            raise ValueError("invalid editorial beat")
        _text(beat.get("id"), "beat id")
        if beat["id"] in ids:
            raise ValueError("duplicate beat id")
        ids.add(beat["id"])
        _text(beat.get("purpose"), "beat purpose")
        _text(beat.get("contribution"), "beat contribution")
    covered = set()
    for shot in plan["shots"]:
        _text(shot.get("purpose"), "shot purpose")
        _text(shot.get("camera"), "shot camera")
        refs = shot.get("beat_ids")
        if (not isinstance(refs, list) or not refs
                or any(not isinstance(ref, str) for ref in refs)
                or len(set(refs)) != len(refs) or not set(refs) <= ids):
            raise ValueError("shot beat_ids must uniquely reference known beats")
        covered.update(refs)
    if covered != ids:
        raise ValueError("editorial beats are not all covered by shots")


def review_ready(review, preview_sha256, edl_sha256):
    """Changing the actual roughcut or its EDL invalidates a previous decision."""
    if not isinstance(review, dict):
        return False
    return (review.get("preview_sha256") == preview_sha256
            and review.get("edl_sha256") == edl_sha256
            and review.get("watched_at_original_speed") is True
            and review.get("listened_to_full_audio") is True
            and review.get("decision") == "continue"
            and isinstance(review.get("notes"), str)
            and bool(review["notes"].strip()))
