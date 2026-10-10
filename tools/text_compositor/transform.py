"""Perspective geometry and local illumination; no OpenCV or network access."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def validate_quad(quad: Sequence[Sequence[float]]) -> np.ndarray:
    """Require a convex TL/TR/BR/BL quad (clockwise in image coordinates)."""
    points = np.asarray(quad, dtype=np.float64)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError("quad must contain four finite x,y pairs")
    edges = np.roll(points, -1, axis=0) - points
    turns = edges[:, 0] * np.roll(edges[:, 1], -1) - edges[:, 1] * np.roll(edges[:, 0], -1)
    if np.any(turns <= 1e-8):
        raise ValueError("quad must be nondegenerate, convex and ordered TL TR BR BL")
    return points


def plane_size(quad: Sequence[Sequence[float]]) -> tuple[int, int]:
    """Use mean opposite-edge lengths; never resize a rendered text bitmap."""
    q = validate_quad(quad)
    lengths = np.linalg.norm(np.roll(q, -1, axis=0) - q, axis=1)
    return max(2, round((lengths[0] + lengths[2]) / 2)), max(2, round((lengths[1] + lengths[3]) / 2))


def plane_corners(size: tuple[int, int]) -> np.ndarray:
    width, height = size
    if width < 2 or height < 2:
        raise ValueError("plane dimensions must both be at least 2")
    return np.array(((0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)), dtype=float)


def _normalization(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    center = points.mean(axis=0)
    distance = np.linalg.norm(points - center, axis=1).mean()
    if distance <= 1e-12:
        raise ValueError("degenerate transform points")
    scale = math.sqrt(2) / distance
    matrix = np.array(((scale, 0, -scale * center[0]), (0, scale, -scale * center[1]), (0, 0, 1)))
    return (points - center) * scale, matrix


def homography(source: Sequence[Sequence[float]], target: Sequence[Sequence[float]]) -> np.ndarray:
    """Solve a normalized 8-equation projective map from source to target."""
    source, target = np.asarray(source, dtype=float), np.asarray(target, dtype=float)
    if source.shape != (4, 2) or target.shape != (4, 2) or not (np.isfinite(source).all() and np.isfinite(target).all()):
        raise ValueError("transform requires two sets of four finite points")
    src, src_norm = _normalization(source)
    dst, dst_norm = _normalization(target)
    rows, values = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows.extend(((x, y, 1, 0, 0, 0, -u * x, -u * y), (0, 0, 0, x, y, 1, -v * x, -v * y)))
        values.extend((u, v))
    try:
        normalized = np.append(np.linalg.solve(np.asarray(rows), values), 1).reshape(3, 3)
        matrix = np.linalg.inv(dst_norm) @ normalized @ src_norm
    except np.linalg.LinAlgError as error:
        raise ValueError("degenerate perspective transform") from error
    if abs(matrix[2, 2]) < 1e-12:
        raise ValueError("perspective denominator is singular")
    return matrix / matrix[2, 2]


def project_points(matrix: np.ndarray, points: Sequence[Sequence[float]]) -> np.ndarray:
    homogeneous = np.column_stack((np.asarray(points, dtype=float), np.ones(len(points))))
    result = homogeneous @ matrix.T
    if np.any(np.abs(result[:, 2]) < 1e-12):
        raise ValueError("point lies at projective infinity")
    return result[:, :2] / result[:, 2:3]


def find_transform(source: Sequence[Sequence[float]], target: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """Eight Pillow coefficients; caller passes output points then input points."""
    return tuple(float(value) for value in homography(source, target).flat)[:8]


def quad_mask(size: tuple[int, int], quad: Sequence[Sequence[float]]) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon([tuple(p) for p in validate_quad(quad)], fill=255)
    return mask


@dataclass(frozen=True)
class Lighting:
    mean_rgb: tuple[float, float, float]
    luminance: float
    gain: float
    tint: tuple[float, float, float]


def sample_lighting(base: Image.Image, quad: Sequence[Sequence[float]]) -> Lighting:
    """Sample visible quad pixels, ignoring fully transparent base pixels."""
    rgba = np.asarray(base.convert("RGBA"), dtype=np.float64)
    mask = np.asarray(quad_mask(base.size, quad)) > 0
    weights = rgba[..., 3][mask] / 255
    if weights.size == 0 or weights.sum() == 0:
        raise ValueError("quad has no visible base pixels to sample")
    mean = np.average(rgba[..., :3][mask], axis=0, weights=weights) / 255
    luminance = float(mean @ np.array((0.2126, 0.7152, 0.0722)))
    # Preserve readability on dark paint while reacting to actual scene brightness.
    gain = 0.62 + 0.38 * math.sqrt(luminance)
    neutral = max(float(mean.mean()), 0.05)
    tint = np.clip(1 + 0.08 * (mean / neutral - 1), 0.92, 1.08)
    return Lighting(tuple(float(c) for c in mean), luminance, gain, tuple(float(c) for c in tint))


def match_lighting(layer: Image.Image, lighting: Lighting, emissive: bool = False) -> Image.Image:
    pixels = np.asarray(layer.convert("RGBA"), dtype=np.float64).copy()
    gain = 0.90 + 0.10 * lighting.gain if emissive else lighting.gain
    pixels[..., :3] *= gain * np.asarray(lighting.tint)
    return Image.fromarray(np.clip(np.rint(pixels), 0, 255).astype(np.uint8))


def warp_layer(layer: Image.Image, quad: Sequence[Sequence[float]], output_size: tuple[int, int], feather: float = 0.7) -> Image.Image:
    """Inverse-map premultiplied RGBA and soften edges by about 1–2px."""
    q = validate_quad(quad)
    if not math.isfinite(feather) or feather < 0:
        raise ValueError("feather must be finite and nonnegative")
    coefficients = find_transform(q, plane_corners(layer.size))
    warped = layer.convert("RGBA").convert("RGBa").transform(
        output_size, Image.Transform.PERSPECTIVE, coefficients,
        resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0, 0),
    )
    if feather:
        warped = warped.filter(ImageFilter.GaussianBlur(feather))
    pixels = np.asarray(warped.convert("RGBA")).copy()
    # Feather remains inside the designated surface, including sticker and glow.
    pixels[..., 3] = np.minimum(pixels[..., 3], np.asarray(quad_mask(output_size, q)))
    return Image.fromarray(pixels)
