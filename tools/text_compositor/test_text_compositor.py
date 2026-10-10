"""Task-scoped tests. Run with the repository's .venv-workflow/bin/python."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

if __package__:
    from .composite import DEFAULT_SHADOW, MODES, composite_text, fit_text, render_text_layer, resolve_font, shade_text_layer
    from .transform import find_transform, homography, match_lighting, plane_corners, plane_size, project_points, quad_mask, sample_lighting, validate_quad, warp_layer
else:
    from composite import DEFAULT_SHADOW, MODES, composite_text, fit_text, render_text_layer, resolve_font, shade_text_layer
    from transform import find_transform, homography, match_lighting, plane_corners, plane_size, project_points, quad_mask, sample_lighting, validate_quad, warp_layer


ROOT = Path(__file__).resolve().parent
QUAD = ((24.25, 28.5), (346.75, 38.25), (320.25, 161.5), (39.5, 147.75))


class PerspectiveTests(unittest.TestCase):
    def test_corner_error_under_half_pixel(self):
        quads = (QUAD, ((0, 0), (399, 0), (399, 199), (0, 199)), ((71, 3), (297, 80), (212, 189), (3, 79)), ((1e6, 2e6), (1e6 + 240, 2e6 + 20), (1e6 + 180, 2e6 + 100), (1e6 + 30, 2e6 + 80)))
        for quad in quads:
            with self.subTest(quad=quad):
                source = plane_corners(plane_size(quad))
                forward = homography(source, quad)
                error = np.max(np.linalg.norm(project_points(forward, source) - quad, axis=1))
                self.assertLess(error, 0.5)
                self.assertLess(error, 1e-6)
                inverse = np.array((*find_transform(quad, source), 1)).reshape(3, 3)
                np.testing.assert_allclose(project_points(inverse, quad), source, atol=1e-6)

    def test_actual_warp_places_corner_markers(self):
        size = (160, 72)
        source = plane_corners(size)
        quad = ((20, 20), (230, 33), (205, 136), (40, 121))
        for x, y in source:
            marker = Image.new("RGBA", size)
            ImageDraw.Draw(marker).ellipse((x - 3, y - 3, x + 3, y + 3), fill="white")
            warped = np.asarray(warp_layer(marker, quad, (260, 160), feather=0))
            destination = project_points(homography(source, quad), [(x, y)])[0]
            px, py = np.rint(destination).astype(int)
            self.assertGreater(warped[max(0, py - 2):py + 3, max(0, px - 2):px + 3, 3].max(), 100)

    def test_invalid_quads(self):
        invalid = (((0, 0), (0, 20), (20, 20), (20, 0)), ((0, 0), (20, 20), (20, 0), (0, 20)), ((0, 0), (20, 0), (20, 0), (0, 20)), ((0, 0), (20, 0), (4, 4), (0, 20)), ((0, 0), (20, 0), (20, float("nan")), (0, 20)), ((0, 0), (1, 1)))
        for quad in invalid:
            with self.subTest(quad=quad), self.assertRaises(ValueError):
                validate_quad(quad)

    def test_degenerate_transform(self):
        with self.assertRaises(ValueError):
            homography(((0, 0), (1, 0), (2, 0), (3, 0)), plane_corners((4, 4)))

    def test_feather_has_intermediate_alpha(self):
        quad = ((10, 10), (60, 10), (60, 50), (10, 50))
        image = warp_layer(Image.new("RGBA", (51, 41), "white"), quad, (80, 70))
        alpha = np.asarray(image)[..., 3]
        self.assertTrue(np.any((alpha > 0) & (alpha < 255)))
        self.assertTrue(np.all(alpha[np.asarray(quad_mask(image.size, quad)) == 0] == 0))

    def test_premultiplied_warp_avoids_dark_halo(self):
        layer = Image.new("RGBA", (80, 40))
        ImageDraw.Draw(layer).rectangle((25, 10, 55, 30), fill="white")
        result = np.asarray(warp_layer(layer, ((0, 0), (79, 0), (79, 39), (0, 39)), layer.size))
        edge = (result[..., 3] > 30) & (result[..., 3] < 230)
        self.assertTrue(edge.any())
        self.assertGreaterEqual(result[..., :3][edge].min(), 248)


class LightingTests(unittest.TestCase):
    def test_samples_polygon_instead_of_bounding_box(self):
        image = Image.new("RGB", (120, 100), "#F00000")
        quad = ((60, 5), (113, 50), (60, 93), (7, 50))
        ImageDraw.Draw(image).polygon(quad, fill="#408060")
        lighting = sample_lighting(image, quad)
        np.testing.assert_allclose(lighting.mean_rgb, np.array((64, 128, 96)) / 255, atol=1e-10)

    def test_brightness_and_tint_respond_to_base(self):
        quad = ((0, 0), (39, 0), (39, 39), (0, 39))
        dark = sample_lighting(Image.new("RGB", (40, 40), "#182020"), quad)
        bright = sample_lighting(Image.new("RGB", (40, 40), "#EFEFDF"), quad)
        warm = sample_lighting(Image.new("RGB", (40, 40), "#D09040"), quad)
        self.assertLess(dark.gain, bright.gain)
        self.assertGreater(warm.tint[0], warm.tint[2])

    def test_matching_preserves_alpha_and_does_not_mutate(self):
        layer = Image.new("RGBA", (40, 40), (240, 200, 150, 100))
        original = layer.tobytes()
        lighting = sample_lighting(Image.new("RGB", layer.size, "#203030"), ((0, 0), (39, 0), (39, 39), (0, 39)))
        result = match_lighting(layer, lighting)
        self.assertEqual(result.getchannel("A").tobytes(), layer.getchannel("A").tobytes())
        self.assertEqual(layer.tobytes(), original)
        self.assertLess(result.getpixel((20, 20))[0], 240)
        self.assertGreater(match_lighting(layer, lighting, True).getpixel((20, 20))[0], result.getpixel((20, 20))[0])

    def test_no_visible_samples_rejected(self):
        for image, quad in ((Image.new("RGB", (10, 10)), ((20, 20), (30, 20), (30, 30), (20, 30))), (Image.new("RGBA", (10, 10)), ((0, 0), (9, 0), (9, 9), (0, 9)))):
            with self.subTest(quad=quad), self.assertRaises(ValueError):
                sample_lighting(image, quad)


class LayoutTests(unittest.TestCase):
    def assert_fits(self, layout, size):
        left, top, right, bottom = layout.bounds
        self.assertGreaterEqual(left, layout.padding)
        self.assertGreaterEqual(top, layout.padding)
        self.assertLessEqual(right, size[0] - layout.padding)
        self.assertLessEqual(bottom, size[1] - layout.padding)

    def test_chinese_long_line_shrinks_to_width(self):
        layout = fit_text("义乌国际商贸城欢迎您参观采购", (160, 55), 80)
        self.assertLess(layout.font_size, 80)
        self.assert_fits(layout, (160, 55))

    def test_short_line_keeps_requested_size(self):
        self.assertEqual(fit_text("店招", (300, 100), 42).font_size, 42)

    def test_height_drives_multiline_fit(self):
        size = (260, 40)
        layout = fit_text("第一行\n第二行\n第三行", size, 60)
        self.assertLess(layout.font_size, 20)
        self.assertAlmostEqual(layout.line_step / layout.font_size, 1.15)
        self.assert_fits(layout, size)

    def test_blank_lines_and_single_glyph(self):
        for text in ("\n中文\n", "单", "A\n\nB"):
            with self.subTest(text=text):
                self.assert_fits(fit_text(text, (80, 60), 30), (80, 60))

    def test_invalid_font_sizes_and_empty_text(self):
        for size in (0, -1, 2.5, True):
            with self.subTest(size=size), self.assertRaises(ValueError):
                fit_text("中文", (80, 40), size)
        for text in ("", " \n ", "A\tB", "A\x00B"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                fit_text(text, (80, 40))

    def test_too_small_and_impossibly_long_text(self):
        for text, size in (("店招", (2, 2)), ("中文" * 200, (8, 8))):
            with self.subTest(size=size), self.assertRaises(ValueError):
                fit_text(text, size)

    def test_large_requested_size_is_bounded(self):
        self.assert_fits(fit_text("中文", (120, 60), 1_000_000), (120, 60))

    def test_modes_render_and_screen_has_uniform_cells(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                image, layout = render_text_layer("中文 ABC\n欢迎 123", (260, 100), 42, mode=mode)
                self.assertEqual(image.size, (260, 100))
                self.assertIsNotNone(image.getbbox())
                self.assert_fits(layout, image.size)
                if mode == "screen":
                    self.assertEqual(layout.widths[0], layout.cell_width * len("中文 ABC"))
        _, label = render_text_layer("铭牌", (260, 100), 42, mode="label")
        self.assertLess(label.font_size, 42)

    def test_ink_stays_within_canvas_padding(self):
        for text in ("义乌国际商贸城", "jÁg\n设备", "中文\n第二行"):
            image, layout = render_text_layer(text, (180, 90), 70)
            left, top, right, bottom = image.getbbox()
            self.assertGreaterEqual(left, layout.padding - 1)
            self.assertGreaterEqual(top, layout.padding - 1)
            self.assertLessEqual(right, 180 - layout.padding + 1)
            self.assertLessEqual(bottom, 90 - layout.padding + 1)

    def test_font_override_and_unknown_mode(self):
        path = resolve_font("中文", "signboard")
        self.assertEqual(fit_text("中文", (160, 80), font_path=path).font_path, str(path))
        with self.assertRaises(ValueError):
            fit_text("中文", (160, 80), font_path=ROOT / "missing-font.otf")
        with self.assertRaises(ValueError):
            fit_text("中文", (160, 80), mode="unknown")


class SurfaceShadingTests(unittest.TestCase):
    def setUp(self):
        self.layer = Image.new("RGBA", (101, 81))
        ImageDraw.Draw(self.layer).rectangle((40, 30, 60, 50), fill=(220, 200, 180, 255))
        self.mask = self.layer.getchannel("A")

    @staticmethod
    def centroid(image):
        alpha = np.asarray(image.getchannel("A"), dtype=float)
        y, x = np.indices(alpha.shape)
        return np.array(((x * alpha).sum(), (y * alpha).sum())) / alpha.sum()

    def test_five_pixel_shadow_softness_strength_and_no_mutation(self):
        original, original_mask = self.layer.tobytes(), self.mask.tobytes()
        blank = Image.new("RGBA", self.layer.size)
        weak = shade_text_layer(blank, self.mask, shadow=0.2, ao=0)
        strong = shade_text_layer(blank, self.mask, shadow=0.6, ao=0)
        offset = self.centroid(strong) - self.centroid(self.layer)
        np.testing.assert_allclose(offset, (3, 4), atol=0.02)
        self.assertAlmostEqual(np.linalg.norm(offset), 5, places=2)
        alpha = np.asarray(strong)[..., 3]
        self.assertTrue(np.any((alpha > 0) & (alpha < alpha.max())))
        self.assertGreater(np.asarray(strong)[..., 3].sum(), np.asarray(weak)[..., 3].sum())
        self.assertEqual(self.layer.tobytes(), original)
        self.assertEqual(self.mask.tobytes(), original_mask)

    def test_shadow_rotates_with_quad_plane(self):
        quad = ((150, 20), (150, 120), (70, 120), (70, 20))
        blank = Image.new("RGBA", self.layer.size)
        cast = shade_text_layer(blank, self.mask, shadow=0.6, ao=0)
        warped = warp_layer(cast, quad, (180, 160), feather=0)
        reference = warp_layer(self.layer, quad, (180, 160), feather=0)
        np.testing.assert_allclose(self.centroid(warped) - self.centroid(reference), (-4, 3), atol=0.15)
        # Also check a skewed perspective plane, using the projected source point.
        quad = ((20, 15), (160, 45), (135, 135), (30, 110))
        warped = warp_layer(cast, quad, (180, 160), feather=0)
        expected = project_points(homography(plane_corners(self.layer.size), quad), [(53, 44)])[0]
        np.testing.assert_allclose(self.centroid(warped), expected, atol=1)

    def test_ao_is_narrow_and_subtle_with_shadow_disabled(self):
        result = shade_text_layer(self.layer, self.mask, shadow=0)
        self.assertEqual(result.getpixel((50, 40)), self.layer.getpixel((50, 40)))
        edge = result.getpixel((40, 40))
        self.assertLess(edge[0], 220)
        self.assertGreater(edge[0], 198)
        self.assertGreater(result.getpixel((39, 40))[3], 0)
        self.assertLessEqual(result.getpixel((39, 40))[3], 26)
        self.assertEqual(result.getpixel((34, 40))[3], 0)
        self.assertEqual(shade_text_layer(self.layer, self.mask, shadow=0, ao=0).tobytes(), self.layer.tobytes())

    def test_empty_mask_and_clipped_shadow_do_not_wrap(self):
        empty = Image.new("L", self.layer.size)
        self.assertEqual(shade_text_layer(self.layer, empty).tobytes(), self.layer.tobytes())
        corner = Image.new("L", self.layer.size)
        ImageDraw.Draw(corner).rectangle((96, 76, 100, 80), fill=255)
        shaded = shade_text_layer(Image.new("RGBA", self.layer.size), corner, shadow=1, ao=0)
        self.assertEqual(np.asarray(shaded)[:20, :20, 3].max(), 0)

    def test_invalid_strengths_and_masks_rejected(self):
        for value in (-1, 1.1, float("nan"), float("inf"), True, None, "0.2"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "shadow"):
                shade_text_layer(self.layer, self.mask, shadow=value)
        with self.assertRaisesRegex(ValueError, "ao"):
            shade_text_layer(self.layer, self.mask, ao=-0.1)
        for mask in (Image.new("RGB", self.layer.size), Image.new("L", (2, 2))):
            with self.assertRaisesRegex(ValueError, "glyph_alpha"):
                shade_text_layer(self.layer, mask)


class CompositeTests(unittest.TestCase):
    def setUp(self):
        self.base = Image.new("RGB", (400, 220), "#486056")

    def test_same_input_identical_pixels_png_and_input_unchanged(self):
        before = self.base.tobytes()
        quad = np.array(QUAD)
        saved_quad = quad.copy()
        for mode in MODES:
            with self.subTest(mode=mode):
                first = composite_text(self.base, "义乌国际商贸城\n测试 ABC", quad, mode=mode)
                second = composite_text(self.base, "义乌国际商贸城\n测试 ABC", quad, mode=mode)
                self.assertEqual(first.tobytes(), second.tobytes())
                a, b = BytesIO(), BytesIO()
                first.save(a, format="PNG")
                second.save(b, format="PNG")
                self.assertEqual(a.getvalue(), b.getvalue())
        self.assertEqual(self.base.tobytes(), before)
        np.testing.assert_array_equal(quad, saved_quad)

    def test_output_changes_only_inside_quad(self):
        mask = np.asarray(quad_mask(self.base.size, QUAD)) > 0
        base = np.asarray(self.base.convert("RGBA"))
        for mode in MODES:
            with self.subTest(mode=mode):
                composed = np.asarray(composite_text(self.base, "欢迎光临", QUAD, mode=mode))
                np.testing.assert_array_equal(composed[~mask], base[~mask])
                self.assertTrue(np.any(composed[mask] != base[mask]))

    def test_different_content_changes_output(self):
        a = composite_text(self.base, "欢迎光临", QUAD)
        b = composite_text(self.base, "义乌商贸", QUAD)
        self.assertNotEqual(a.tobytes(), b.tobytes())

    def test_default_shadow_and_zero_keep_ao_in_all_modes(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                default = composite_text(self.base, "ABC", QUAD, mode=mode)
                explicit = composite_text(self.base, "ABC", QUAD, mode=mode, shadow=DEFAULT_SHADOW)
                no_shadow = composite_text(self.base, "ABC", QUAD, mode=mode, shadow=0)
                layer, _ = render_text_layer("ABC", plane_size(QUAD), mode=mode)
                old = Image.alpha_composite(self.base.convert("RGBA"), warp_layer(match_lighting(layer, sample_lighting(self.base, QUAD), mode == "screen"), QUAD, self.base.size))
                self.assertEqual(default.tobytes(), explicit.tobytes())
                self.assertNotEqual(default.tobytes(), no_shadow.tobytes())
                self.assertNotEqual(no_shadow.tobytes(), old.tobytes())

    def test_shadow_strength_rejected_by_api(self):
        for value in (-0.01, 1.01, float("nan"), float("inf"), True, "0.2"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "shadow"):
                composite_text(self.base, "ABC", QUAD, shadow=value)

    def test_partly_offscreen_quad_and_rgba_base(self):
        base = self.base.convert("RGBA")
        base.putalpha(180)
        quad = ((-20, 10), (260, 20), (240, 120), (-10, 110))
        result = composite_text(base, "中文屏幕", quad, mode="screen")
        self.assertEqual(result.mode, "RGBA")
        self.assertEqual(result.size, base.size)
        self.assertEqual(base.getpixel((0, 0))[3], 180)

    def test_cli_contract_and_errors(self):
        # All temporary files stay inside the authorized new tool directory.
        with tempfile.TemporaryDirectory(prefix="cli-test-", dir=ROOT) as directory:
            directory = Path(directory)
            base, output = directory / "base.png", directory / "out.png"
            self.base.save(base)
            command = [sys.executable, str(ROOT / "composite.py"), "--base", str(base), "--text", "义乌国际商贸城", "--quad", "24,28", "346,38", "320,161", "39,147", "--font-size", "42", "--color", "#E8C468", "--mode", "signboard", "--out", str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["mode"], "signboard")
            self.assertEqual(json.loads(result.stdout)["shadow"], DEFAULT_SHADOW)
            with Image.open(output) as image:
                self.assertEqual(image.size, self.base.size)
            result = subprocess.run(command[:-1] + [str(base)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("--out must differ", result.stderr)
            bad_size = command.copy()
            bad_size[bad_size.index("--font-size") + 1] = "0"
            result = subprocess.run(bad_size, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)
            for strength in (0, 0.6):
                result = subprocess.run(command + ["--shadow", str(strength)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["shadow"], strength)
                with Image.open(output) as image:
                    expected = composite_text(self.base, "义乌国际商贸城", ((24, 28), (346, 38), (320, 161), (39, 147)), shadow=strength)
                    self.assertEqual(image.tobytes(), expected.tobytes())
            for strength in ("-0.1", "1.1", "nan", "inf"):
                result = subprocess.run(command + ["--shadow", strength], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("shadow must be finite and between 0 and 1", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
