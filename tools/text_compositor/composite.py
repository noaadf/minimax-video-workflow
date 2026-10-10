"""Offline CLI: render real glyphs, fit a plane, match light, perspective-paste."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from numbers import Real
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

if __package__:
    from .transform import match_lighting, plane_size, sample_lighting, validate_quad, warp_layer
else:
    from transform import match_lighting, plane_size, sample_lighting, validate_quad, warp_layer


MODES = ("signboard", "label", "screen", "sticker")
DEFAULT_SHADOW = 0.28
SHADOW_OFFSET = (3, 4)  # 5px along the quad's local right/down axes.
SHADOW_BLUR = 1.8
AO_STRENGTH = 0.10
AO_BLUR = 0.85
CJK_FONTS = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf",
    "/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)
MONO_FONTS = (
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
)


def resolve_font(text: str, mode: str, font_path: str | Path | None = None) -> Path:
    if font_path is not None:
        path = Path(font_path).expanduser()
        if not path.is_file():
            raise ValueError(f"font does not exist: {path}")
        return path
    candidates = MONO_FONTS + CJK_FONTS if mode == "screen" and text.isascii() else CJK_FONTS
    for candidate in candidates:
        if Path(candidate).is_file():
            return Path(candidate)
    raise ValueError("No STHeiti/Source Han Sans font found; pass --font /path/to/font.otf")


@dataclass(frozen=True)
class TextLayout:
    font_path: str
    font_size: int
    line_step: float
    padding: int
    widths: tuple[float, ...]
    bounds: tuple[float, float, float, float]
    cell_width: float | None


def _metrics(lines: list[str], font: ImageFont.FreeTypeFont, mode: str):
    boxes = [font.getbbox(line) if line else (0, 0, 0, 0) for line in lines]
    cell = max((max(font.getlength(char), font.getbbox(char)[2] - font.getbbox(char)[0]) for line in lines for char in line), default=1) if mode == "screen" else None
    widths = tuple(len(line) * cell if cell is not None else max(font.getlength(line), box[2] - box[0]) for line, box in zip(lines, boxes))
    step = font.size * 1.15
    # Common ink top preserves baseline relationships; blank lines keep their space.
    ink_boxes = [box for line, box in zip(lines, boxes) if line]
    top = min(box[1] for box in ink_boxes)
    bottom = max(index * step + box[3] for index, (line, box) in enumerate(zip(lines, boxes)) if line)
    return boxes, widths, step, top, bottom - top, cell


def fit_text(text: str, size: tuple[int, int], font_size: int = 42, mode: str = "signboard", font_path: str | Path | None = None) -> TextLayout:
    """Reduce integer font size; keep native glyph proportions and 1.15 leading."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must contain visible characters")
    if any(ord(char) < 32 and char != "\n" for char in text):
        raise ValueError("text supports newlines, but no other control characters")
    if isinstance(font_size, bool) or not isinstance(font_size, int) or font_size < 1:
        raise ValueError("font_size must be a positive integer")
    width, height = size
    if width < 2 or height < 2:
        raise ValueError("plane dimensions must both be at least 2")
    path = resolve_font(text, mode, font_path)
    padding = max(1, round(min(width, height) * (0.10 if mode in ("label", "sticker") else 0.08)))
    available_width, available_height = width - 2 * padding, height - 2 * padding
    if available_width <= 0 or available_height <= 0:
        raise ValueError("quad is too small for text and padding")
    # Label deliberately starts smaller than a signboard at the same requested size.
    factor = {"signboard": 1.0, "label": 0.72, "screen": 0.86, "sticker": 0.92}[mode]
    upper = min(max(1, math.floor(font_size * factor)), 2 * max(width, height))
    lines = text.split("\n")
    low, high, best = 1, upper, None
    while low <= high:
        candidate = (low + high) // 2
        font = ImageFont.truetype(str(path), candidate)
        metrics = _metrics(lines, font, mode)
        if max(metrics[1]) <= available_width and metrics[4] <= available_height:
            best = candidate, metrics
            low = candidate + 1
        else:
            high = candidate - 1
    if best is None:
        raise ValueError("text cannot fit quad even at font size 1; enlarge quad or shorten text")
    chosen, (_, widths, step, _, block_height, cell) = best
    block_width = max(widths)
    left, top = (width - block_width) / 2, (height - block_height) / 2
    return TextLayout(str(path), chosen, step, padding, widths, (left, top, left + block_width, top + block_height), cell)


def _render_text_parts(text: str, size: tuple[int, int], font_size: int = 42, color: str = "#E8C468", mode: str = "signboard", font_path: str | Path | None = None) -> tuple[Image.Image, Image.Image, TextLayout]:
    layout = fit_text(text, size, font_size, mode, font_path)
    fill = ImageColor.getcolor(color, "RGBA")
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    width, height = size
    if mode == "sticker":
        inset = max(1, layout.padding // 3)
        draw.rounded_rectangle((inset, inset, width - 1 - inset, height - 1 - inset), radius=max(1, layout.padding // 2), fill=(245, 241, 222, 242))
    if mode == "label":
        inset = max(1, layout.padding // 3)
        draw.rectangle((inset, inset, width - 1 - inset, height - 1 - inset), outline=fill, width=1)
    glyphs = Image.new("RGBA", size, (0, 0, 0, 0))
    glyph_draw = ImageDraw.Draw(glyphs)
    font = ImageFont.truetype(layout.font_path, layout.font_size)
    lines = text.split("\n")
    boxes, _, _, ink_top, _, _ = _metrics(lines, font, mode)
    y_origin = layout.bounds[1] - ink_top
    for index, (line, box, line_width) in enumerate(zip(lines, boxes, layout.widths)):
        x = (width - line_width) / 2
        y = y_origin + index * layout.line_step
        if layout.cell_width is not None:
            for char in line:
                glyph_box = font.getbbox(char)
                glyph_width = glyph_box[2] - glyph_box[0]
                glyph_draw.text((x + (layout.cell_width - glyph_width) / 2 - glyph_box[0], y), char, font=font, fill=fill)
                x += layout.cell_width
        else:
            glyph_draw.text((x - box[0], y), line, font=font, fill=fill)
    if mode == "screen":
        glow = glyphs.convert("RGBa").filter(ImageFilter.GaussianBlur(1.2)).convert("RGBA")
        glow.putalpha(glow.getchannel("A").point(lambda a: round(a * 0.32)))
        layer = Image.alpha_composite(layer, glow)
    return layer, glyphs, layout


def render_text_layer(text: str, size: tuple[int, int], font_size: int = 42, color: str = "#E8C468", mode: str = "signboard", font_path: str | Path | None = None) -> tuple[Image.Image, TextLayout]:
    """Render the original unshaded layer; preserve the existing two-value API."""
    decoration, glyphs, layout = _render_text_parts(text, size, font_size, color, mode, font_path)
    return Image.alpha_composite(decoration, glyphs), layout


def _validate_strength(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be finite and between 0 and 1")


def shade_text_layer(layer: Image.Image, glyph_alpha: Image.Image, shadow: float = DEFAULT_SHADOW, ao: float = AO_STRENGTH) -> Image.Image:
    """Add plane-space cast shadow and subtle inner/outer glyph contact AO.

    Only glyph coverage casts shadows: borders, sticker backing and screen glow
    do not. Apply before warp_layer so direction and softness follow the quad.
    """
    _validate_strength(shadow, "shadow")
    _validate_strength(ao, "ao")
    if glyph_alpha.mode != "L" or glyph_alpha.size != layer.size:
        raise ValueError("glyph_alpha must be an L mask matching the layer size")
    alpha = np.asarray(glyph_alpha, dtype=float) / 255
    blurred = np.asarray(glyph_alpha.filter(ImageFilter.GaussianBlur(AO_BLUR)), dtype=float) / 255
    pixels = np.asarray(layer.convert("RGBA")).copy()
    # The narrow inner band darkens ink; the outer band anchors it to the plane.
    inner = np.maximum(alpha - blurred, 0)
    pixels[..., :3] = np.rint(pixels[..., :3] * (1 - ao * inner[..., None])).astype(np.uint8)
    contact = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    contact.putalpha(Image.fromarray(np.rint(255 * ao * np.maximum(blurred - alpha, 0)).astype(np.uint8)))
    cast = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    if shadow:
        shifted = Image.new("L", layer.size, 0)
        shifted.paste(glyph_alpha, SHADOW_OFFSET)  # No wraparound at canvas edges.
        soft = shifted.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
        cast.putalpha(soft.point(lambda a: round(a * shadow)))
    underneath = Image.alpha_composite(cast, contact)
    return Image.alpha_composite(underneath, Image.fromarray(pixels))


def composite_text(base: Image.Image, text: str, quad: Sequence[Sequence[float]], font_size: int = 42, color: str = "#E8C468", mode: str = "signboard", font_path: str | Path | None = None, shadow: float = DEFAULT_SHADOW) -> Image.Image:
    """Pure function: neither base nor arguments are mutated; returns RGBA."""
    _validate_strength(shadow, "shadow")
    points = validate_quad(quad)
    lighting = sample_lighting(base, points)
    decoration, glyphs, _ = _render_text_parts(text, plane_size(points), font_size, color, mode, font_path)
    lit_glyphs = match_lighting(glyphs, lighting, emissive=mode == "screen")
    lit_decoration = match_lighting(decoration, lighting, emissive=mode == "screen")
    # Cast onto the sticker backing, rather than hiding the shadow below it.
    shaded = Image.alpha_composite(lit_decoration, shade_text_layer(lit_glyphs, glyphs.getchannel("A"), shadow))
    return Image.alpha_composite(base.convert("RGBA"), warp_layer(shaded, points, base.size))


def parse_point(value: str) -> tuple[float, float]:
    try:
        x, y = value.split(",")
        point = float(x), float(y)
        if not all(math.isfinite(c) for c in point):
            raise ValueError
        return point
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a finite x,y pair") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--text", required=True, help="literal text; use actual newlines for multiple lines")
    parser.add_argument("--quad", required=True, nargs=4, type=parse_point, metavar="X,Y")
    parser.add_argument("--font-size", type=int, default=42)
    parser.add_argument("--font", type=Path, help="optional local font path; never downloaded")
    parser.add_argument("--color", default="#E8C468")
    parser.add_argument("--mode", choices=MODES, default="signboard")
    parser.add_argument("--shadow", type=float, default=DEFAULT_SHADOW, help="cast shadow opacity 0..1 (default: 0.28); 0 disables cast shadow, keeps edge AO")
    parser.add_argument("--out", required=True, type=Path, help="PNG output")
    args = parser.parse_args(argv)
    try:
        if args.base.resolve() == args.out.resolve():
            raise ValueError("--out must differ from --base")
        with Image.open(args.base) as source:
            base = source.convert("RGBA")
        result = composite_text(base, args.text, args.quad, args.font_size, args.color, args.mode, args.font, args.shadow)
        layout = fit_text(args.text, plane_size(args.quad), args.font_size, args.mode, args.font)
        result.save(args.out, format="PNG")
    except (ValueError, OSError, Image.DecompressionBombError) as error:
        parser.error(str(error))
    print(json.dumps({"out": str(args.out), "mode": args.mode, "plane_size": plane_size(args.quad), "font_size": layout.font_size, "font": layout.font_path, "shadow": args.shadow}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
