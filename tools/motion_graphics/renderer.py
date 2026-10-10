"""Broadcast-style portrait graphics; Pillow/numpy frames piped to ffmpeg.

Usage: .venv-workflow/bin/python tools/motion_graphics/renderer.py spec.json out.mp4
       [--preview out.png]
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

if __package__:
    from .spec_schema import ANIMATION_SECONDS, FPS, SpecError, frame_count, freeze_time, load_spec, validate_spec
else:
    from spec_schema import ANIMATION_SECONDS, FPS, SpecError, frame_count, freeze_time, load_spec, validate_spec

WIDTH, HEIGHT, SCALE = 540, 960, 2
GOLD = (232, 196, 104)
WHITE = (229, 234, 237)
GRAY = (153, 164, 174)
MUTED = (112, 127, 140)
LINE = (53, 69, 81)
FONT_PATHS = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/SourceHanSansSC-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/adobe-source-han-sans/SourceHanSansSC-Regular.otf",
)
NUMBER_PATH = "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf"


def ease_out(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return 1 - (1 - value) ** 3


def ease_in_out(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return 4 * value ** 3 if value < 0.5 else 1 - (-2 * value + 2) ** 3 / 2


def progress(t: float, reveal: dict, duration: float = 0.45, delay: float = 0) -> float:
    return ease_out((t - reveal["t"] - delay) / duration)


def number_text(value: float, decimals: int = 0) -> str:
    return f"{value:,.{decimals}f}"


def precision(value: float) -> int:
    return 0 if value == int(value) else min(2, len(str(value).split(".")[-1]))


@lru_cache(maxsize=64)
def font(size: int, numeric: bool = False) -> ImageFont.FreeTypeFont:
    cjk_path = next((p for p in FONT_PATHS if Path(p).is_file()), None)
    if cjk_path is None:
        raise RuntimeError("STHeiti or Source Han Sans / Noto Sans CJK font is required")
    path = NUMBER_PATH if numeric and Path(NUMBER_PATH).is_file() else cjk_path
    return ImageFont.truetype(path, size * SCALE)


class Canvas:
    """Draw in logical pixels onto a supersampled transparent layer."""

    def __init__(self) -> None:
        self.image = Image.new("RGBA", (WIDTH * SCALE, HEIGHT * SCALE))
        self.draw = ImageDraw.Draw(self.image)

    def reveal(self, layer: Canvas, bounds: tuple, amount: float, axis: str = "x") -> None:
        """Composite an opaque layer through an expanding rectangular mask."""
        amount = max(0.0, min(1.0, amount))
        x0, y0, x1, y1 = bounds
        if axis == "x":
            x1 = x0 + (x1 - x0) * amount
        else:
            y1 = y0 + (y1 - y0) * amount
        rect = tuple(round(v * SCALE) for v in (x0, y0, x1, y1))
        if rect[2] > rect[0] and rect[3] > rect[1]:
            self.image.alpha_composite(layer.image.crop(rect), (rect[0], rect[1]))

    @staticmethod
    def color(rgb: tuple, alpha: float = 1) -> tuple:
        return (*rgb, round(255 * max(0.0, min(1.0, alpha))))

    def line(self, xy: list | tuple, color: tuple = LINE, alpha: float = 1, width: float = 1) -> None:
        self.draw.line([(round(x * SCALE), round(y * SCALE)) for x, y in xy],
                       fill=self.color(color, alpha), width=max(1, round(width * SCALE)))

    def box(self, xy: tuple, color: tuple, alpha: float = 1, radius: float = 0,
            outline: tuple | None = None) -> None:
        if xy[2] <= xy[0] or xy[3] <= xy[1] or alpha <= 0:
            return
        rect = tuple(round(v * SCALE) for v in xy)
        self.draw.rounded_rectangle(rect, radius=round(radius * SCALE),
                                    fill=self.color(color, alpha),
                                    outline=self.color(outline, alpha) if outline else None,
                                    width=SCALE)

    def circle(self, x: float, y: float, radius: float, color: tuple, alpha: float = 1) -> None:
        self.draw.ellipse(tuple(round(v * SCALE) for v in (x - radius, y - radius, x + radius, y + radius)),
                          fill=self.color(color, alpha))

    def text(self, text: str, x: float, y: float, size: int, color: tuple = WHITE,
             alpha: float = 1, numeric: bool = False, max_width: float | None = None,
             anchor: str = "lt") -> float:
        selected = font(size, numeric)
        if max_width is not None:
            while selected.getlength(text) / SCALE > max_width and size > 14:
                size -= 1
                selected = font(size, numeric)
            if selected.getlength(text) / SCALE > max_width:
                raise SpecError(f"text exceeds safe area: {text!r}")
        self.draw.text((round(x * SCALE), round(y * SCALE)), text,
                       font=selected, fill=self.color(color, alpha), anchor=anchor)
        return selected.getlength(text) / SCALE

    def paragraph(self, text: str, x: float, y: float, size: int, width: float,
                  max_lines: int, color: tuple = WHITE, alpha: float = 1) -> None:
        selected = font(size)
        lines: list[str] = []
        line = ""
        for char in text:
            if line and selected.getlength(line + char) / SCALE > width:
                lines.append(line)
                line = ""
            line += char
        if line:
            lines.append(line)
        if len(lines) > max_lines:
            raise SpecError(f"text exceeds {max_lines}-line safe area: {text!r}")
        for i, line in enumerate(lines):
            self.text(line, x, y + i * (size + 9), size, color, alpha)


@lru_cache(maxsize=1)
def background() -> Image.Image:
    y, x = np.mgrid[0:HEIGHT * SCALE, 0:WIDTH * SCALE].astype(np.float32)
    vertical = y / (HEIGHT * SCALE - 1)
    start = np.array([14, 23, 32], dtype=np.float32)
    end = np.array([22, 34, 46], dtype=np.float32)
    rgb = start + vertical[..., None] * (end - start)
    vignette = 1 - 0.27 * np.clip(((x / (WIDTH * SCALE) - 0.5) / 0.7) ** 2
                                + ((vertical - 0.45) / 0.8) ** 2, 0, 1)
    rgb *= vignette[..., None]
    # Deterministic fine grain eliminates flat gradients and never flickers.
    grain = np.random.default_rng(7).normal(0, 0.28, rgb.shape[:2])
    rgb = np.clip(rgb + grain[..., None], 0, 255).astype(np.uint8)
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    for gx in range(48, 493, 36):
        draw.line((gx * SCALE, 48 * SCALE, gx * SCALE, 912 * SCALE), fill=(25, 38, 49), width=1)
    for gy in range(48, 913, 36):
        draw.line((48 * SCALE, gy * SCALE, 492 * SCALE, gy * SCALE), fill=(25, 38, 49), width=1)
    return image


class Renderer:
    def __init__(self, spec: dict) -> None:
        self.spec = validate_spec(spec)
        self.reveals = sorted(spec["reveals"], key=lambda r: r["t"])
        self.by_kind = {r["kind"]: r for r in self.reveals}
        self.steps = [r for r in self.reveals if r["kind"] == "step"]
        self.freeze = freeze_time(spec)
        font(32)  # Fail before ffmpeg starts if Chinese fonts are missing.

    def frame(self, t: float) -> Image.Image:
        t = min(t, self.freeze)
        c = Canvas()
        self.header(c, t)
        for reveal in self.reveals:
            if t < reveal["t"]:
                continue
            kind = reveal["kind"]
            if kind == "stat":
                p = progress(t, reveal)
                color = GOLD if reveal.get("emphasis") == "primary" else GRAY
                layer = Canvas()
                layer.paragraph(reveal["text"], 48, 220, 23, 444, 2, color)
                c.reveal(layer, (48, 216, 492, 284), p)
            elif kind == "comparison":
                self.comparison(c, t, reveal)
            elif kind == "number_roll":
                self.number_roll(c, t, reveal)
            elif kind == "step":
                self.step(c, t, reveal, self.steps.index(reveal))
            elif kind == "trend":
                self.trend(c, t, reveal)
            elif kind == "source_note":
                p = progress(t, reveal)
                c.line([(48, 835), (48 + 444 * p, 835)], LINE)
                layer = Canvas()
                layer.paragraph(reveal["text"], 48, 851, 16, 444, 2, GRAY)
                c.reveal(layer, (48, 847, 492, 894), p)
        result = background().copy()
        result.paste(c.image, (0, 0), c.image.getchannel("A"))
        return result.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)

    def header(self, c: Canvas, t: float) -> None:
        reveal = self.by_kind.get("title", {"t": 0.0})
        p = progress(t, reveal)
        offset = (1 - p) * 18
        names = {"stat_highlight": "关键数据", "comparison": "效率对比", "process": "技术路径", "trend": "趋势观察"}
        c.box((48, 68 + offset, 52, 84 + offset), GOLD, p)
        c.text(names[self.spec["layout"]], 63, 68 + offset, 15, GRAY, p)
        c.text(self.spec["shot_id"], 492, 68 + offset, 15, MUTED, p, numeric=True, anchor="rt", max_width=170)
        layer = Canvas()
        layer.paragraph(self.spec["title"], 48, 113 + offset, 34, 444, 2, WHITE)
        c.reveal(layer, (48, 109, 492, 195), p, axis="y")
        c.line([(48, 199), (48 + 444 * p, 199)], LINE, p)
        c.text("数据视角", 48, 896, 13, MUTED, p)
        c.text("MOTION / 24", 492, 896, 13, MUTED, p, numeric=True, anchor="rt")

    def comparison(self, c: Canvas, t: float, r: dict) -> None:
        compact = self.spec["layout"] == "stat_highlight"
        maximum = max(r["left"]["value"], r["right"]["value"])
        for index, side in enumerate(("left", "right")):
            item = r[side]
            p = progress(t, r, 0.45, index * 0.1)
            if p <= 0:
                continue
            growth = ease_in_out((t - r["t"] - index * 0.1 - 1 / FPS) / (0.85 - 1 / FPS))
            color = GRAY if index == 0 else GOLD
            offset = (1 - p) * 18
            if compact:
                y = 340 + index * 126 + offset
                c.box((48, y - 20, 492, y + 88), (22, 35, 46), radius=8, outline=LINE)
                layer = Canvas()
                layer.text(item["label"], 68, y, 23, WHITE, max_width=185)
                c.reveal(layer, (68, y - 2, 260, y + 32), p)
                value = number_text(float(item["value"]) * growth, precision(float(item["value"])))
                c.text(value + " " + item["unit"], 472, y, 28, color, anchor="rt", max_width=210)
                c.box((68, y + 46, 472, y + 62), (31, 46, 58), radius=3)
                c.box((68, y + 46, 68 + 404 * item["value"] / maximum * growth, y + 62), color, radius=3)
            else:
                x = 48 + index * 230
                y = 318 + offset
                c.box((x, y, x + 214, y + 303), (22, 35, 46), radius=10, outline=LINE)
                c.line([(x + 20, y + 2), (x + 20 + 174 * p, y + 2)], color, width=2)
                layer = Canvas()
                layer.text(item["label"], x + 20, y + 25, 23, WHITE, max_width=174)
                c.reveal(layer, (x + 20, y + 22, x + 194, y + 57), p)
                value = item["value"] * growth
                c.text(number_text(value, precision(float(item["value"]))), x + 17, y + 80, 136,
                       color, numeric=True, max_width=180)
                c.text(item["unit"], x + 22, y + 225, 22, GRAY, max_width=170)
                c.box((x + 20, y + 272, x + 194, y + 279), (40, 54, 66), radius=2)
                c.box((x + 20, y + 272, x + 20 + 174 * item["value"] / maximum * growth, y + 279), color, radius=2)

    def number_roll(self, c: Canvas, t: float, r: dict) -> None:
        p = progress(t, r)
        if p <= 0:
            return
        # Hold the starting value for one sampled frame, then finish on schedule.
        growth = ease_out((t - r["t"] - 1 / FPS) / (ANIMATION_SECONDS["number_roll"] - 1 / FPS))
        value = r["from"] + (r["to"] - r["from"]) * growth
        final_text = number_text(float(r["to"]), precision(float(r["to"])))
        # The input sentence stays a label; the exact number is drawn once below.
        label = re.sub(re.escape(final_text.replace(",", "")) + r"\s*" + re.escape(r["suffix"]), "", r["text"]).strip()
        layout = self.spec["layout"]
        y = 661 if layout == "comparison" else 640 if layout == "stat_highlight" else 614
        offset = (1 - p) * 20
        panel = layout in {"stat_highlight", "comparison"}
        left = 70 if panel else 48
        width = 402 if panel else 444
        if panel:
            top = y - 25 + offset
            c.box((48, top, 492, 818 + offset), (27, 42, 53), radius=10, outline=LINE)
            c.box((48, top + 12, 52, top + 12 + (806 - (y - 25) - 12) * p), GOLD)
        layer = Canvas()
        layer.text(label, left, y + offset, 23, WHITE, max_width=width)
        c.reveal(layer, (left, y - 2 + offset, left + width, y + 32 + offset), p)
        main_size = 114 if layout == "comparison" else 140 if panel else 152
        text = number_text(value, max(precision(float(r["from"])), precision(float(r["to"]))))
        suffix_size = 60 if r["suffix"] == "%" else 35
        suffix_width = font(suffix_size).getlength(r["suffix"]) / SCALE
        available = width - suffix_width - 12
        while font(main_size, True).getlength(text) / SCALE > available and main_size > 14:
            main_size -= 1
        text_width = c.text(text, left, y + 48 + offset, main_size, GOLD, numeric=True,
                            max_width=available)
        number_box = font(main_size, True).getbbox(text, anchor="lt")
        suffix_box = font(suffix_size).getbbox(r["suffix"], anchor="lt")
        baseline_offset = (number_box[3] - number_box[1] - suffix_box[3] + suffix_box[1]) / SCALE
        c.text(r["suffix"], left + text_width + 10, y + 48 + baseline_offset + offset,
               suffix_size, GOLD)

    def step(self, c: Canvas, t: float, r: dict, index: int) -> None:
        p = progress(t, r)
        details = progress(t, r, 0.55, 0.1)
        y = 299 + index * 160 + (1 - p) * 16
        if index:
            c.line([(76, y - 63), (76, y - 18)], GOLD, p * 0.7, 2)
        c.circle(76, y + 24, 27, (28, 43, 55), p)
        c.text(f"{index + 1:02d}", 76, y + 7, 36, GOLD, p, numeric=True, anchor="mt")
        layer = Canvas()
        layer.text(r["title"], 124, y + 1, 27, WHITE, max_width=368)
        c.reveal(layer, (124, y - 2, 492, y + 39), p)
        layer = Canvas()
        layer.paragraph(r["text"], 124, y + 51, 20, 368, 3, GRAY)
        c.reveal(layer, (124, y + 48, 492, y + 135), details)
        c.line([(124, y + 134), (124 + 368 * details, y + 134)], LINE, details)

    def trend(self, c: Canvas, t: float, r: dict) -> None:
        p = progress(t, r)
        # Constant point-by-point travel makes the first segment visible early.
        growth = max(0.0, min(1.0, (t - r["t"]) / ANIMATION_SECONDS["trend"]))
        points = r["points"]
        maximum = max(item["value"] for item in points)
        # Explicit zero baseline keeps the chart quantitatively honest.
        ceiling = maximum * 1.12
        x0, x1, y0, y1 = 100, 456, 326, 555
        c.text(r["unit"], 48, 290, 17, GRAY, p, max_width=444)
        for i in range(4):
            y = y1 - (y1 - y0) * i / 3
            c.line([(x0, y), (x1, y)], LINE, p * 0.8)
            c.text(number_text(ceiling * i / 3), 88, y - 6, 13, MUTED, p, numeric=True, anchor="rt", max_width=40)
        xy = [(x0 + (x1 - x0) * i / (len(points) - 1), y1 - item["value"] / ceiling * (y1 - y0))
              for i, item in enumerate(points)]
        total = growth * (len(points) - 1)
        for i in range(len(xy) - 1):
            fraction = max(0, min(1, total - i))
            if fraction <= 0:
                continue
            a, b = xy[i], xy[i + 1]
            endpoint = (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)
            # Subtle gold area ribbons; same single accent hue as the line.
            poly = [a, endpoint, (endpoint[0], y1), (a[0], y1)]
            c.draw.polygon([(round(x * SCALE), round(y * SCALE)) for x, y in poly], fill=c.color(GOLD, p * 0.035))
            c.line([a, endpoint], GOLD, p, 3)
            if fraction < 1:
                c.circle(*endpoint, 4, GOLD, p)
        for i, ((x, y), item) in enumerate(zip(xy, points)):
            stagger = progress(t, r, 0.45, i * 0.1)
            c.text(item["label"], x, y1 + 20, 15, GRAY, stagger, anchor="mt", max_width=70)
            if total >= i:
                c.circle(x, y, 5, GOLD, p)
                # Endpoint labels face inward and remain below the chart's top edge.
                label_y = max(y0 + 8, y - 29)
                label_x = x1 - 8 if i == len(points) - 1 else x + 8 if i == 0 else x
                anchor = "rt" if i == len(points) - 1 else "lt" if i == 0 else "mt"
                c.text(number_text(float(item["value"]), precision(float(item["value"]))),
                       label_x, label_y, 18, WHITE, p, numeric=True, anchor=anchor, max_width=62)


def render_video(spec: dict, output: Path, preview: Path | None = None) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required on PATH")
    if output.suffix.lower() != ".mp4":
        raise SpecError("output must use .mp4 extension")
    if preview is not None and preview.suffix.lower() != ".png":
        raise SpecError("preview must use .png extension")
    for path in (output, preview):
        if path is not None and path.exists():
            raise FileExistsError(f"refusing to overwrite existing file: {path}")
    renderer = Renderer(spec)
    # Preflight the completed composition to catch text overflow before encoding.
    final = renderer.frame(renderer.freeze)
    for path in (output, preview):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".render-", suffix=".mp4", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    process: subprocess.Popen | None = None
    try:
        with tempfile.TemporaryFile(mode="w+b", dir=output.parent) as log:
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
                       "-pix_fmt", "rgb24", "-s:v", f"{WIDTH}x{HEIGHT}", "-r", str(FPS),
                       "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "medium", "-g", "1", "-qp", "18",
                       "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-threads", "2", str(temporary)]
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
            try:
                assert process.stdin is not None
                count = frame_count(spec)
                for index in range(count):
                    t = index / FPS
                    frame = final if t >= renderer.freeze else renderer.frame(t)
                    process.stdin.write(frame.tobytes())
                    if index % FPS == 0:
                        print(f"{spec['shot_id']}: {index}/{count} frames", file=sys.stderr, flush=True)
                process.stdin.close()
                returncode = process.wait()
            except BrokenPipeError as exc:
                process.wait()
                log.seek(0)
                raise RuntimeError(f"ffmpeg failed: {log.read().decode(errors='replace')}") from exc
            if returncode:
                log.seek(0)
                raise RuntimeError(f"ffmpeg failed ({returncode}): {log.read().decode(errors='replace')}")
        # Hard-link creation is atomic and cannot replace another session's file.
        os.link(temporary, output)
        if preview is not None:
            with preview.open("xb") as handle:
                final.save(handle, format="PNG")
    finally:
        if process is not None and process.poll() is None:
            process.terminate()  # Only our child encoder, never production workers.
            process.wait()
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a silent 540×960 / 24fps declarative graphics shot")
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--preview", type=Path, help="save the final held composition as PNG")
    args = parser.parse_args()
    try:
        spec = load_spec(args.spec)
        render_video(spec, args.output, args.preview)
    except (SpecError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"saved {args.output} ({frame_count(spec)} frames, {frame_count(spec) / FPS:.3f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
