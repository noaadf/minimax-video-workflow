"""Read-only preflight of explicit editorial evidence, not a vision/story grader.

Inputs are independent sidecars. Never reads/writes queues, approvals or manifests.
Exit 2: contradiction; 3: evidence pending; 0: structural preflight only.
"""
import argparse
import json
import math
from pathlib import Path


def validate(plan):
    issues = []

    def issue(rule, shot, detail, severity="block"):
        issues.append(dict(rule=rule, shot=shot, detail=detail, severity=severity))

    duration = plan["duration"]
    if not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be finite and positive")
    cursor, ids = _shot_evidence(plan, issue)
    if abs(cursor - duration) > plan.get("frame_tolerance", 1 / 24):
        issue("PICTURE_DURATION", None, "Shot duration sum differs from intended picture duration.")
    voice_end = _voice_evidence(plan, duration, issue)
    _music_evidence(plan, duration, voice_end, issue)
    status = "blocked" if any(i["severity"] == "block" for i in issues) else "evidence_pending" if issues else "structural_preflight_only"
    return {"status": status, "issues": issues, "automatic_visual_recognition": False,
            "not_an_approval": True, "shot_count": len(ids)}


def _cut_evidence(shot, previous, sid, issue):
    if not shot.get("cut_reason"):
        issue("CUT_REASON", sid, "Declare the information or reaction gained by this cut.")
    relation = shot.get("relation", {})
    if previous and not relation.get("type"):
        issue("CAUSAL_CUT", sid, "Declare continuous action, time ellipsis or labeled evidence insert.")
    if relation.get("type") == "ellipsis" and not relation.get("cue"):
        issue("ELLIPSIS_CUE", sid, "Changed state requires a perceivable before/after or time cue.")
    if shot.get("role") == "evidence_insert" and not shot.get("label"):
        issue("INSERT_LABEL", sid, "A schematic evidence insert needs a viewer-facing label.")
    return relation


def _scene_evidence(shot, relation, scenes, scene, sid, issue):
    remembered = scenes.get(scene)
    if remembered:
        if remembered["axis"] != shot["axis"] and not relation.get("reestablish_space"):
            issue("SPACE_AXIS", sid, "Camera side changed without a new spatial establishing view.")
        if remembered["actors"] != shot["actors"]:
            issue("ACTOR_BINDING", sid, "Actor identity/wardrobe/position changed within the scene.")
        if remembered["state_out"] != shot["state_in"]:
            if relation.get("type") != "ellipsis" or not relation.get("cue"):
                issue("STATE_HANDOFF", sid, "Object state does not match the last shot in this scene.")
    if shot["state_in"] != shot["state_out"] and not shot.get("state_change_event"):
        issue("STATE_EVENT", sid, "In-shot state mutation has no visible causal event.")


def _action_evidence(shot, length, sid, issue):
    complete = shot.get("action_complete_local")
    if complete is None:
        issue("ACTION_EVIDENCE", sid, "Annotate actual action/reaction completion after reviewing the clip.", "pending")
    elif complete < 0 or complete > length:
        issue("TRUNCATED_ACTION", sid, "Cut precedes the declared action/reaction completion.")


def _observation_evidence(shot, length, sid, issue):
    for observation in shot.get("observations", []):
        if not observation.get("evidence"):
            issue("OBSERVATION_EVIDENCE", sid, "Observed states require a frame/clip evidence reference.", "pending")
            continue
        if not 0 <= observation["local_time"] <= length:
            raise ValueError("observation outside shot")
        # Events need their own before/after annotations; do not invent recognition.
        if not shot.get("state_change_event"):
            for key, value in observation["state"].items():
                if key in shot["state_in"] and value != shot["state_in"][key]:
                    issue("OBSERVED_MUTATION", sid, f"{key}: expected {shot['state_in'][key]!r}, observed {value!r}.")


def _voice_evidence(plan, duration, issue):
    voice_end = 0.0
    for voice in plan["voice"]:
        if not (0 <= voice["start"] < voice["end"] <= duration):
            issue("VOICE_BOUNDARY", None, "Narration interval outside picture.")
        if voice["start"] < voice_end:
            issue("VOICE_OVERLAP", None, "Narration clips overlap.")
        if abs((voice["end"] - voice["start"]) - voice["measured_duration"]) > plan.get("frame_tolerance", 1 / 24):
            issue("VOICE_MEASUREMENT", None, "Narration timing differs from measured delivered audio.")
        voice_end = max(voice_end, voice["end"])
    return voice_end


def _music_evidence(plan, duration, voice_end, issue):
    music = plan["music"]
    if music.get("source_audio_enabled") is not False:
        issue("CLEAN_STEMS", None, "Unreviewed model source audio must be discarded.")
    if music["fade_start"] < voice_end:
        issue("MUSIC_VOICE_END", None, "Project policy keeps music through the final voiced phrase.")
    if abs(music["fade_end"] - duration) > plan.get("frame_tolerance", 1 / 24):
        issue("MUSIC_ENDPOINT", None, "Fade must end at the final picture endpoint.")
    if music.get("loop_after_fade"):
        issue("MUSIC_EFFECT_ORDER", None, "Loop/trim first, then apply the endpoint fade.")
    if duration - voice_end < plan.get("minimum_tail", 0.8):
        issue("ENDING_BREATH", None, "Project policy requires a short settled image after the final line.")


def _shot_evidence(plan, issue):
    previous = None
    scenes = {}
    cursor = 0.0
    ids = set()
    for shot in plan["shots"]:
        sid = shot["id"]
        if sid in ids:
            raise ValueError("duplicate shot id")
        ids.add(sid)
        length = shot["duration"]
        if not math.isfinite(length) or length <= 0:
            raise ValueError("shot duration must be finite and positive")
        end = cursor + length
        relation = _cut_evidence(shot, previous, sid, issue)
        scene = shot["scene"]
        _scene_evidence(shot, relation, scenes, scene, sid, issue)
        _action_evidence(shot, length, sid, issue)
        _observation_evidence(shot, length, sid, issue)
        scenes[scene] = shot
        previous = shot
        cursor = end
    return (cursor, ids)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sidecar", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.sidecar.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(2 if result["status"] == "blocked" else 3 if result["status"] == "evidence_pending" else 0)
