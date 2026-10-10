"""Finite local animatic from existing assets and speech. No queue or generation."""
import copy
import json
import math
from pathlib import Path

from . import media
from .core.io import sha256, write_json

_RECIPE_SHA256 = sha256(__file__)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def source_paths(config):
    paths = {config["narration_path"], config["music_path"]}
    paths.update(row["source_path"] for row in config["shots"])
    paths.update(row['end_source_path'] for row in config['shots'] if row.get('end_source_path'))
    if config.get('locked_audio'):
        paths.add(config['locked_audio']['path'])
    for line in config["caption_plan"]["narration"]:
        if line.get("word_timing_path"):
            paths.add(line["word_timing_path"])
            cue = json.loads(Path(line["word_timing_path"]).read_text(encoding="utf-8"))
            paths.add(cue["source_path"])
    for field in ("source_manifest", "narration_receipt"):
        if config.get("basis", {}).get(field):
            paths.add(config["basis"][field])
    return {str(Path(path).resolve()) for path in paths}


def freeze(config):
    config = copy.deepcopy(config)
    config["input_sha256"] = {path: sha256(path) for path in sorted(source_paths(config))}
    return config


def _validate_row_window(row):
    start, seconds = row.get("source_start", 0), row.get("duration_seconds")
    if not _finite(start) or start < 0 or not _finite(seconds) or seconds <= 0:
        raise ValueError("invalid roughcut shot source window")
    if row.get("kind", "video") not in ("video", "image"):
        raise ValueError("roughcut supports video or image")
    if row.get('end_source_path') and row.get('kind') != 'image':
        raise ValueError('end keyframe is only valid for image animatics')


def _validate_bound_inputs(config):
    paths = source_paths(config)
    if set(config.get("input_sha256", {})) != paths:
        raise ValueError("freeze all roughcut inputs before rendering")
    for path, expected in config["input_sha256"].items():
        if not Path(path).is_file() or sha256(path) != expected:
            raise ValueError("roughcut input changed: " + path)


def _validate_header(config):
    if config.get("schema_version") != 1 or config.get("kind") != "editorial_roughcut":
        raise ValueError("roughcut schema_version=1 and kind=editorial_roughcut required")
    length = config.get("duration_seconds")
    if not _finite(length) or not 1 <= length <= 120:
        raise ValueError("roughcut duration must be 1..120 seconds")
    rows = config.get("shots")
    if not isinstance(rows, list) or not rows:
        raise ValueError("roughcut needs shots")
    return length, rows


def _validate_rows(rows):
    ids = set()
    for row in rows:
        if (not isinstance(row.get("id"), str) or not row["id"] or row["id"] in ids
                or not isinstance(row.get("purpose"), str) or not row["purpose"].strip()):
            raise ValueError("roughcut needs unique shot ids and editorial purposes")
        ids.add(row["id"])
        _validate_row_window(row)

def _validate_inputs(config, rows, length):
    _validate_bound_inputs(config)
    for stem in ("narration_path", "music_path"):
        if abs(media.duration(config[stem]) - length) > .1:
            raise ValueError("roughcut independent stem must cover exact timeline: " + stem)
    for row in rows:
        if row.get("kind", "video") == "video":
            if row.get('source_fit_policy')=='whole_action':
                media.whole_action_filter(row['source_path'],row['duration_seconds'],row.get('source_start',0))
            elif media.duration(row["source_path"]) + .04 < row.get("source_start", 0) + row["duration_seconds"]:
                raise ValueError("roughcut source too short; no frozen or looped padding")


def validate(config):
    length, rows = _validate_header(config)
    _validate_rows(rows)
    if abs(sum(row["duration_seconds"] for row in rows) - length) > .001:
        raise ValueError("roughcut EDL does not sum to total duration")
    if config["caption_plan"].get("duration_seconds") != length:
        raise ValueError("roughcut caption timeline differs from EDL")
    music_gain = config.get("music_gain", 1)
    if not _finite(music_gain) or not 0 <= music_gain <= 1:
        raise ValueError("invalid roughcut music_gain")
    _validate_inputs(config, rows, length)
    from .caption_timing import events
    if config.get('locked_audio'):
        media.verify_locked_audio(config)
    # Fail before making video files if cues are missing, stale or out of range.
    cues = events(config["caption_plan"])
    if any(not 0 <= cue["start"] < cue["end"] <= length for cue in cues):
        raise ValueError("roughcut captions exceed timeline")
    return cues


def render(config, destination):
    cues = validate(config)
    out = Path(destination).resolve()
    for path in source_paths(config):
        if Path(path).is_relative_to(out):
            raise ValueError("roughcut output must not contain source assets")
    if out.exists() and any(out.iterdir()):
        raise ValueError("roughcut output exists; use a new destination")
    out.mkdir(parents=True, exist_ok=True)
    fps, previous_frames, elapsed = 24, 0, 0
    rendered = []
    for i, row in enumerate(config["shots"]):
        sources = [row['source_path']]
        if row.get('end_source_path'):
            sources.append(row['end_source_path'])
        for part, source in enumerate(sources):
            elapsed += row["duration_seconds"] / len(sources)
            frames = round(elapsed * fps) - previous_frames
            previous_frames += frames
            if frames < 1:
                raise ValueError("roughcut shot is shorter than one frame")
            piece = out / f"piece_{i:03d}_{part}.mp4"
            if row.get("kind", "video") == "image":
                inputs = ["-loop", "1", "-framerate", fps, "-i", source]
            else:
                inputs = ["-ss", row.get("source_start", 0), "-i", source]
            from .framing import filter_for
            vf=filter_for(540,960,config['caption_plan'].get('framing_policy'))
            if row.get('source_fit_policy')=='whole_action':vf=media.whole_action_filter(source,row['duration_seconds'],row.get('source_start',0))+','+vf
            media.ff(*inputs, "-an", "-vf", vf,
                     "-frames:v", frames, "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23",
                     "-pix_fmt", "yuv420p", piece)
            actual_frames = round(media.duration(piece) * fps)
            if abs(actual_frames - frames) > 1:
                raise ValueError("source did not yield the requested roughcut frames")
            rendered.append(piece)
    concat = out / "concat.txt"
    concat.write_text("".join(f"file '{piece.name}'\n" for piece in rendered), encoding="utf-8")
    picture = out / "picture.mp4"
    media.ff("-f", "concat", "-safe", "0", "-i", concat, "-an", "-c:v", "copy", picture)
    caption_plan = copy.deepcopy(config["caption_plan"])
    caption_plan["shots"] = config["shots"]
    ass = out / "captions.ass"
    media.captions(caption_plan, ass)
    with ass.open("a") as f:
        f.write(f"Dialogue: 1,0:00:00.00,{media.timestamp(config['duration_seconds'])},ShotLabel,,0,0,0,," +
                r"{\pos(48,215)}粗剪预览 · 待审" + "\n")
    music_gain = config.get("music_gain", 1)
    # Explicit maps and silent pictures prevent accidental raw-source audio mixing.
    filters = f"[2:a]volume={music_gain}[bed];[1:a][bed]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.891:level=0[a]"
    output = out / "preview.mp4"
    # Use a relative fixed filename so arbitrary directory punctuation cannot inject ASS filter options.
    from .color_policy import filters as color_filters
    grade, color_receipt = color_filters(config['caption_plan'])
    write_json(out/'color_grading.json', {'policy':config['caption_plan'].get('color_policy'),
               'shots':color_receipt,'applied_before_captions':True})
    final_vf = (grade+',' if grade else '') + 'ass=filename=captions.ass'
    video_args = ['-vf',final_vf,'-map','0:v:0','-t',config['duration_seconds'],
                  '-c:v','libx264','-threads','2','-preset','veryfast','-crf','22','-pix_fmt','yuv420p']
    if config.get('locked_audio'):
        media.ff('-i',picture,'-i',config['locked_audio']['path'],*video_args,
                 '-map','1:a:0','-c:a','copy','-movflags','+faststart',output,cwd=out)
    else:
        media.ff("-i", picture, "-i", config["narration_path"], "-i", config["music_path"],
                 "-filter_complex", filters, *video_args, '-map','[a]',
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",output, cwd=out)
    if abs(media.duration(output) - config["duration_seconds"]) > .1:
        raise ValueError("roughcut duration differs from actual audio timeline")
    write_json(out / "edl.json", config)
    report = {"status": "roughcut_pending_full_speed_and_listening_review", "preview": str(output),
              "preview_sha256": sha256(output), "edl_sha256": sha256(out / "edl.json"),
              "input_sha256": config["input_sha256"], "duration_seconds": media.duration(output),
              "requested_duration_seconds": config["duration_seconds"], "source_audio_in_mix": False,
              "caption_events": cues, "approved": False, "production_changed": False,
              "picture_frame_count": previous_frames, "recipe_sha256": _RECIPE_SHA256}
    write_json(out / "report.json", report)
    write_json(out / "review.json", {"preview_sha256": report["preview_sha256"], "edl_sha256": report["edl_sha256"],
               "watched_at_original_speed": False, "listened_to_full_audio": False, "decision": "pending", "notes": ""})
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = render(json.loads(Path(args.config).read_text(encoding="utf-8")), args.out)
    print(json.dumps({key: result[key] for key in ["status", "preview", "duration_seconds"]}, ensure_ascii=False))
