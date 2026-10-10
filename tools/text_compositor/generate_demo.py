"""Draw a wordless 9:16 storefront and reproduce all three review examples."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

if __package__:
    from .composite import AO_BLUR, AO_STRENGTH, DEFAULT_SHADOW, SHADOW_BLUR, SHADOW_OFFSET, composite_text, fit_text, render_text_layer, resolve_font
    from .transform import match_lighting, plane_size, sample_lighting, warp_layer
else:
    from composite import AO_BLUR, AO_STRENGTH, DEFAULT_SHADOW, SHADOW_BLUR, SHADOW_OFFSET, composite_text, fit_text, render_text_layer, resolve_font
    from transform import match_lighting, plane_size, sample_lighting, warp_layer


DEMO = Path(__file__).resolve().parent / "demo"
EXAMPLES = (
    {"mode": "signboard", "text": "义乌国际商贸城", "quad": ((111, 284), (618, 309), (612, 410), (111, 382)), "font_size": 62, "color": "#E8C468"},
    {"mode": "label", "text": "设备铭牌\n额定电压 220V", "quad": ((118, 481), (275, 482), (275, 568), (118, 567)), "font_size": 25, "color": "#E7E6D7"},
    {"mode": "screen", "text": "STATUS: READY\nTEMP  24.6 C\nLINK  ONLINE", "quad": ((470, 678), (620, 680), (614, 785), (470, 782)), "font_size": 21, "color": "#99EFCB"},
)


def draw_base() -> Image.Image:
    width, height = 720, 1280
    rows = np.linspace(0, 1, height)[:, None, None]
    top = np.array((135, 163, 168), dtype=float)[None, None, :]
    bottom = np.array((224, 208, 179), dtype=float)[None, None, :]
    pixels = np.broadcast_to(top * (1 - rows) + bottom * rows, (height, width, 3)).astype(np.uint8).copy()
    image = Image.fromarray(pixels)
    d = ImageDraw.Draw(image)
    # Adjacent facades, unmarked cables and glazing.
    d.polygon(((0, 154), (110, 192), (109, 988), (0, 1050)), fill="#B8B1A0")
    d.polygon(((640, 179), (719, 115), (719, 1100), (632, 1000)), fill="#BBBBAE")
    d.line(((0, 98), (260, 151), (720, 65)), fill="#66746F", width=3)
    d.line(((0, 115), (400, 174), (720, 111)), fill="#778781", width=2)
    d.polygon(((85, 199), (652, 225), (643, 1000), (91, 973)), fill="#CFC7B1")
    d.polygon(((101, 234), (636, 260), (630, 923), (101, 907)), fill="#DAD2BB")
    for y in range(430, 920, 62):
        d.line(((103, y), (631, y + 16)), fill="#BFB79F", width=1)
    # Signboard plane is deliberately blank.
    d.polygon(((103, 283), (627, 308), (621, 423), (103, 393)), fill="#6A695C")
    d.polygon(EXAMPLES[0]["quad"], fill="#344B45")
    d.line(((111, 284), (618, 309)), fill="#607163", width=3)
    d.line(((111, 383), (612, 411)), fill="#263A37", width=3)
    # Door / window frames and warm, dim interior.
    d.polygon(((299, 447), (626, 458), (616, 941), (297, 928)), fill="#3C4946")
    d.polygon(((311, 460), (610, 470), (603, 923), (310, 912)), fill="#736E57")
    d.polygon(((325, 480), (442, 484), (440, 900), (325, 895)), fill="#8A8163")
    d.polygon(((456, 486), (596, 490), (592, 908), (454, 902)), fill="#596458")
    for y in (549, 611, 845):
        d.line(((328, y), (590, y + 8)), fill="#454E42", width=7)
        for x in range(337, 580, 39):
            d.rectangle((x, y - 32, x + 26, y - 4), fill=(145 + x % 30, 125 + x % 20, 82 + x % 35), outline="#646149", width=1)
    d.polygon(((310, 459), (361, 461), (390, 914), (310, 912)), fill="#909078")
    d.line(((448, 469), (445, 917)), fill="#344943", width=9)
    d.line(((433, 698), (433, 750)), fill="#C1BA9A", width=5)
    # Screen bezel and blank display.
    d.polygon(((460, 669), (629, 671), (622, 797), (460, 793)), fill="#A19F89")
    d.polygon(((465, 673), (625, 675), (619, 792), (465, 789)), fill="#273F3A")
    d.polygon(EXAMPLES[2]["quad"], fill="#10282A")
    d.line(((473, 680), (617, 682)), fill="#284742", width=2)
    # Electrical housing and its unmarked label.
    d.rounded_rectangle((108, 466, 285, 660), radius=8, fill="#ADB09D", outline="#818C7B", width=3)
    d.polygon(EXAMPLES[1]["quad"], fill="#68756A")
    for x, y in ((113, 474), (280, 474), (113, 652), (280, 652)):
        d.ellipse((x - 2, y - 2, x + 2, y + 2), fill="#525F55")
    for y in range(600, 638, 6):
        d.line(((132, y), (257, y)), fill="#697866", width=2)
    d.line(((195, 660), (195, 850), (111, 850)), fill="#8B8E77", width=5)
    # Awning ribs, pavement and planters.
    d.polygon(((96, 425), (633, 447), (654, 477), (74, 452)), fill="#AA9E7B")
    for x in range(100, 640, 38):
        d.line(((x, 427 + (x - 96) // 24), (x - 20, 453 + (x - 96) // 24)), fill="#6B7A64", width=17)
    d.polygon(((0, 1050), (94, 951), (643, 973), (720, 1090), (720, 1280), (0, 1280)), fill="#A3A394")
    d.polygon(((286, 930), (618, 943), (645, 974), (265, 958)), fill="#676D60")
    for end in (-150, 55, 285, 520, 820):
        d.line(((360, 966), (end, 1280)), fill="#858B7B", width=2)
    for y in (1030, 1116, 1220):
        d.line(((0, y), (719, y + 25)), fill="#8C9180", width=2)
    for x, y, scale in ((149, 952, 1.0), (654, 982, 0.78)):
        d.ellipse((x - 61 * scale, y - 12, x + 64 * scale, y + 26), fill="#858D77")
        d.polygon(((x - 33 * scale, y - 74), (x + 33 * scale, y - 74), (x + 24 * scale, y), (x - 24 * scale, y)), fill="#A58163")
        for index, (dx, dy) in enumerate(((-22, -95), (14, -115), (-6, -143), (35, -97), (-32, -126), (10, -83), (29, -138))):
            dx, dy = dx * scale, dy * scale
            d.line(((x, y - 67), (x + dx, y + dy)), fill="#5B7253", width=3)
            d.ellipse((x + dx - 14 * scale, y + dy - 25 * scale, x + dx + 14 * scale, y + dy + 5 * scale), fill=(67 + index * 3, 100 + index * 3, 64 + index * 2))
    return image


def comparison(base: Image.Image, composed: Image.Image, mode: str, before: str = "BEFORE / NO TEXT", after: str | None = None) -> Image.Image:
    width, height = base.size
    result = Image.new("RGB", (width * 2 + 16, height + 58), "#F2EEDD")
    result.paste(base.convert("RGB"), (0, 58))
    result.paste(composed.convert("RGB"), (width + 16, 58))
    font = ImageFont.truetype(str(resolve_font("BEFORE", "screen")), 22)
    d = ImageDraw.Draw(result)
    d.text((24, 16), before, font=font, fill="#344B45")
    d.text((width + 40, 16), after or f"AFTER / {mode.upper()}", font=font, fill="#344B45")
    return result


def main() -> None:
    DEMO.mkdir(parents=True, exist_ok=True)
    base = draw_base()
    base.save(DEMO / "base_signboard.png")
    records, comparisons, details = [], [], []
    for example in EXAMPLES:
        mode = example["mode"]
        composed = composite_text(base, **example)
        path = DEMO / f"{mode}.png"
        composed.save(path)
        # Reproduce r1 without shadow/AO for a controlled same-text comparison.
        layer, _ = render_text_layer(example["text"], plane_size(example["quad"]), example["font_size"], example["color"], mode)
        light = sample_lighting(base, example["quad"])
        old = Image.alpha_composite(base.convert("RGBA"), warp_layer(match_lighting(layer, light, mode == "screen"), example["quad"], base.size))
        old.save(DEMO / f"{mode}_r1.png")
        comparison(old, composed, mode, "R1 / NO SHADOW OR AO", "R2 / SHADOW + EDGE AO").save(DEMO / f"{mode}_r2_comparison.png")
        q = np.asarray(example["quad"])
        box = (max(0, int(q[:, 0].min()) - 16), max(0, int(q[:, 1].min()) - 16), min(base.width, int(q[:, 0].max()) + 17), min(base.height, int(q[:, 1].max()) + 17))
        crop_size = ((box[2] - box[0]) * 3, (box[3] - box[1]) * 3)
        detail = comparison(old.crop(box).resize(crop_size, Image.Resampling.NEAREST), composed.crop(box).resize(crop_size, Image.Resampling.NEAREST), mode, f"R1 / {mode.upper()}", "R2 / SHADOW + EDGE AO")
        detail.save(DEMO / f"{mode}_r2_detail.png")
        details.append(detail.resize((1000, round(detail.height * 1000 / detail.width)), Image.Resampling.LANCZOS))
        pair = comparison(base, composed, mode)
        pair.save(DEMO / f"{mode}_comparison.png")
        comparisons.append(pair.resize((728, 669), Image.Resampling.LANCZOS))
        layout = fit_text(example["text"], plane_size(example["quad"]), example["font_size"], mode)
        records.append({**example, "shadow": DEFAULT_SHADOW, "actual_font_size": layout.font_size, "font": layout.font_path, "plane_size": plane_size(example["quad"]), "sample_luminance": light.luminance, "lighting_gain": light.gain, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    contact = Image.new("RGB", (728, 669 * 3), "#F2EEDD")
    for index, pair in enumerate(comparisons):
        contact.paste(pair, (0, index * 669))
    contact.save(DEMO / "contact_sheet.png")
    r2_contact = Image.new("RGB", (1000, sum(detail.height for detail in details)), "#F2EEDD")
    y = 0
    for detail in details:
        r2_contact.paste(detail, (0, y))
        y += detail.height
    r2_contact.save(DEMO / "r2_contact_sheet.png")
    artifacts = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(DEMO.glob("*.png"))}
    effects = {"shadow_offset_plane_px": SHADOW_OFFSET, "shadow_blur_radius_plane_px": SHADOW_BLUR, "default_shadow": DEFAULT_SHADOW, "ao_strength": AO_STRENGTH, "ao_blur_radius_plane_px": AO_BLUR, "r1_baseline": "original text + light match + warp, without shadow/AO", "detail_scale": 3}
    (DEMO / "manifest.json").write_text(json.dumps({"base_size": base.size, "effects": effects, "examples": records, "artifact_sha256": artifacts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"demo": str(DEMO), "examples": len(records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
