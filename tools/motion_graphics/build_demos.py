"""Render four demos, extract encoded review frames, and verify media properties."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw

from renderer import font
from spec_schema import FPS, frame_count, load_spec

ROOT = Path(__file__).resolve().parent
NAMES = ("stat_highlight", "comparison", "process", "trend")


def run(command: list[str]) -> bytes:
    return subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def verify_and_extract(name: str, destination: Path) -> dict:
    spec = load_spec(ROOT / "demo" / f"{name}.json")
    video = destination / f"{name}.mp4"
    count = frame_count(spec)
    metadata = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)]))
    streams = metadata["streams"]
    assert len(streams) == 1 and streams[0]["codec_type"] == "video", "audio or unexpected streams"
    stream = streams[0]
    assert (stream["width"], stream["height"]) == (540, 960)
    assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
    assert stream["r_frame_rate"] == "24/1" and int(stream["nb_frames"]) == count
    actual_duration = float(stream["duration"])
    assert abs(actual_duration - spec["duration_seconds"]) <= 1 / (2 * FPS) + 0.00001
    review_frames = []
    for percent in (10, 50, 90):
        index = min(count - 1, round(float(spec["duration_seconds"]) * percent / 100 * FPS))
        target = destination / f"{name}_{percent:02d}.png"
        if target.exists():
            raise FileExistsError(f"refusing to overwrite {target}")
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-n", "-i", str(video),
             "-vf", f"select=eq(n\\,{index})", "-frames:v", "1", str(target)])
        with Image.open(target) as image:
            assert image.size == (540, 960)
        review_frames.append({"path": f"demo/{target.name}", "requested_percent": percent,
                              "frame_index": index, "actual_seconds": index / FPS})
    raw = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
               "-vf", f"select=gte(n\\,{count - 12})", "-fps_mode", "passthrough",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
    frame_bytes = 540 * 960 * 3
    assert len(raw) == frame_bytes * 12
    hold_hashes = [hashlib.sha256(raw[i * frame_bytes:(i + 1) * frame_bytes]).hexdigest() for i in range(12)]
    # Fixed-QP all-intra encoding must preserve exact decoded tail-frame equality.
    import numpy as np
    hold = np.frombuffer(raw, dtype=np.uint8).reshape(12, 960, 540, 3).astype(np.int16)
    max_hold_difference = int(np.abs(hold - hold[0]).max())
    assert len(set(hold_hashes)) == 1, f"unexpected motion in final hold: delta {max_hold_difference}"
    return {"layout": name, "width": 540, "height": 960, "fps": 24, "codec": "h264",
            "pixel_format": stream["pix_fmt"], "audio_streams": 0, "frames": count,
            "requested_duration": spec["duration_seconds"], "actual_duration": actual_duration,
            "bytes": video.stat().st_size, "final_hold_frames": 12,
            "decoded_hold_identical": len(set(hold_hashes)) == 1,
            "decoded_hold_max_channel_difference": max_hold_difference, "review_frames": review_frames}


def review_sheet(destination: Path) -> None:
    sheet = Image.new("RGB", (1080, 1536), (14, 23, 32))
    draw = ImageDraw.Draw(sheet)
    for row, percent in enumerate((10, 50, 90)):
        for column, name in enumerate(NAMES):
            x, y = column * 270, row * 512
            draw.text((x + 12, y + 12), f"{name} / {percent}%", font=font(8), fill=(232, 196, 104))
            with Image.open(destination / f"{name}_{percent:02d}.png") as frame:
                sheet.paste(frame.resize((270, 480), Image.Resampling.LANCZOS), (x, y + 32))
    sheet.save(destination / "review_sheet.png")


def entrance_sheet(destination: Path) -> list[dict]:
    """Show encoded early/mid/late entrance frames for the revision audit."""
    rows = (("stat_highlight", "cards", (1.541667, 1.625, 1.833333, 2.5)),
            ("stat_highlight", "result", (4.041667, 4.166667, 4.333333, 5.0)),
            ("comparison", "sync", (1.333333, 1.416667, 1.625, 2.333333)),
            ("trend", "line", (0.45, 0.583333, 0.791667, 1.65)))
    sheet = Image.new("RGB", (1080, 2048), (14, 23, 32))
    draw = ImageDraw.Draw(sheet)
    audit = []
    for row, (name, stage, times) in enumerate(rows):
        for column, requested_time in enumerate(times):
            index = round(requested_time * FPS)
            raw = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i",
                       str(destination / f"{name}.mp4"), "-vf", f"select=eq(n\\,{index})",
                       "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
            assert len(raw) == 540 * 960 * 3
            x, y = column * 270, row * 512
            draw.text((x + 12, y + 12), f"{name}/{stage} {index / FPS:.3f}s",
                      font=font(7), fill=(232, 196, 104))
            frame = Image.frombytes("RGB", (540, 960), raw)
            sheet.paste(frame.resize((270, 480), Image.Resampling.LANCZOS), (x, y + 32))
            audit.append({"layout": name, "stage": stage, "frame_index": index,
                          "actual_seconds": index / FPS})
    sheet.save(destination / "entrance_sheet.png")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="replace only verified demo artifacts")
    args = parser.parse_args()
    names = [f"{name}{suffix}" for name in NAMES
             for suffix in (".mp4", "_final.png", "_10.png", "_50.png", "_90.png")]
    names += ["verification.json", "review_sheet.png", "entrance_sheet.png", "entrance_frames.json"]
    destination = ROOT / "demo"
    if not args.rebuild:
        for name in names:
            if (destination / name).exists():
                raise FileExistsError(f"refusing to overwrite {destination / name}; use --rebuild")
    # Complete all renders and checks before replacing any delivered artifact.
    with tempfile.TemporaryDirectory(prefix=".demos-", dir=ROOT) as temporary:
        staged = Path(temporary)
        results = []
        for name in NAMES:
            subprocess.run([sys.executable, "-B", str(ROOT / "renderer.py"),
                            str(destination / f"{name}.json"), str(staged / f"{name}.mp4"),
                            "--preview", str(staged / f"{name}_final.png")], check=True)
            results.append(verify_and_extract(name, staged))
            print(f"verified {name}", flush=True)
        review_sheet(staged)
        audit = entrance_sheet(staged)
        for name, data in (("verification.json", results), ("entrance_frames.json", audit)):
            (staged / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for name in names:
            if args.rebuild:
                (staged / name).replace(destination / name)
            else:
                # Preserve the CLI's no-overwrite contract, including a racing build.
                os.link(staged / name, destination / name)


if __name__ == "__main__":
    main()
