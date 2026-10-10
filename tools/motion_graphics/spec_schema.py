"""Validate motion-graphics specs without network calls or third-party schemas.

Reveal intervals are entrance animations. Elements persist once revealed. Every
entrance group must be disjoint and finish before the final frozen hold.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

FPS = 24
FINAL_HOLD = 0.5
LAYOUTS = {"stat_highlight", "comparison", "process", "trend"}
ANIMATION_SECONDS = {
    "title": 0.45, "stat": 0.45, "comparison": 0.95,
    "number_roll": 1.0, "source_note": 0.45, "step": 0.65, "trend": 1.2,
}
KINDS = {
    "stat_highlight": {"title", "stat", "comparison", "number_roll", "source_note"},
    "comparison": {"title", "stat", "comparison", "number_roll", "source_note"},
    "process": {"title", "stat", "step", "source_note"},
    "trend": {"title", "stat", "trend", "number_roll", "source_note"},
}


class SpecError(ValueError):
    """A spec cannot be rendered without ambiguity or clipping."""


def _require(obj: dict, keys: tuple[str, ...], where: str) -> None:
    for key in keys:
        if key not in obj:
            raise SpecError(f"{where}.{key}: missing required field")


def _text(value: Any, where: str, limit: int = 80) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{where}: expected non-empty string")
    if len(value) > limit or any(ord(c) < 32 for c in value):
        raise SpecError(f"{where}: maximum {limit} characters, no control characters")
    return value


def _number(value: Any, where: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecError(f"{where}: expected finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise SpecError(f"{where}: expected finite number") from exc
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise SpecError(f"{where}: expected finite number >= {minimum}")
    return result


def _formatted(value: float) -> str:
    return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}".rstrip("0").rstrip(".")


def _display_number(value: Any, where: str, minimum: float | None = None) -> float:
    result = _number(value, where, minimum)
    if len(_formatted(result)) > 6:
        raise SpecError(f"{where}: number too wide for the portrait canvas")
    if not math.isclose(result, round(result, 2), rel_tol=0, abs_tol=1e-9):
        raise SpecError(f"{where}: at most two decimal places, to preserve exact displayed data")
    return result


def frame_count(spec: dict) -> int:
    return round(float(spec["duration_seconds"]) * FPS)


def freeze_time(spec: dict) -> float:
    # The last 12 encoded frames are pixel-identical, even after fps rounding.
    return (frame_count(spec) - round(FINAL_HOLD * FPS)) / FPS


def validate_spec(spec: Any) -> dict:
    if not isinstance(spec, dict):
        raise SpecError("spec: expected JSON object")
    _require(spec, ("shot_id", "duration_seconds", "layout", "title", "reveals"), "spec")
    _text(spec["shot_id"], "spec.shot_id", 32)
    _text(spec["title"], "spec.title", 24)
    duration = _number(spec["duration_seconds"], "spec.duration_seconds", 2)
    if duration > 60:
        raise SpecError("spec.duration_seconds: maximum 60 seconds")
    layout = spec["layout"]
    if not isinstance(layout, str) or layout not in LAYOUTS:
        raise SpecError(f"spec.layout: expected one of {sorted(LAYOUTS)}")
    if not isinstance(spec["reveals"], list) or not spec["reveals"]:
        raise SpecError("spec.reveals: expected non-empty list")
    seen: set[str] = set()
    intervals: list[tuple[float, float, str]] = []
    step_count = 0
    synced_number: tuple[float, float, str] | None = None
    comparison_interval: tuple[float, float, str] | None = None
    for i, reveal in enumerate(spec["reveals"]):
        where = f"spec.reveals[{i}]"
        if not isinstance(reveal, dict):
            raise SpecError(f"{where}: expected object")
        _require(reveal, ("t", "kind"), where)
        start = _number(reveal["t"], f"{where}.t", 0)
        kind = reveal["kind"]
        if not isinstance(kind, str) or kind not in KINDS[layout]:
            raise SpecError(f"{where}.kind: unsupported for {layout}")
        if "sync_with" in reveal:
            if kind != "number_roll" or layout != "comparison" or reveal["sync_with"] != "comparison":
                raise SpecError(f"{where}.sync_with: only comparison number_roll may sync with comparison")
        if kind != "step" and kind in seen:
            raise SpecError(f"{where}: persistent {kind} slot overlaps an earlier reveal")
        seen.add(kind)
        if kind == "step":
            step_count += 1
            _require(reveal, ("title", "text"), where)
            _text(reveal["title"], f"{where}.title", 14)
            _text(reveal["text"], f"{where}.text", 38)
        if kind in {"stat", "number_roll", "source_note"}:
            _require(reveal, ("text",), where)
            _text(reveal["text"], f"{where}.text", 40 if kind == "source_note" else 30)
        if "emphasis" in reveal and (not isinstance(reveal["emphasis"], str)
                                     or reveal["emphasis"] not in {"primary", "secondary"}):
            raise SpecError(f"{where}.emphasis: expected primary or secondary")
        if kind == "number_roll":
            _require(reveal, ("from", "to", "suffix"), where)
            for key in ("from", "to"):
                _display_number(reveal[key], f"{where}.{key}")
            if not isinstance(reveal["suffix"], str) or len(reveal["suffix"]) > 4:
                raise SpecError(f"{where}.suffix: expected string of at most 4 characters")
            if any(ord(c) < 32 for c in reveal["suffix"]):
                raise SpecError(f"{where}.suffix: no control characters")
        if kind == "comparison":
            _require(reveal, ("left", "right"), where)
            for side in ("left", "right"):
                item = reveal[side]
                if not isinstance(item, dict):
                    raise SpecError(f"{where}.{side}: expected object")
                _require(item, ("label", "value", "unit"), f"{where}.{side}")
                _text(item["label"], f"{where}.{side}.label", 12)
                _text(item["unit"], f"{where}.{side}.unit", 4)
                _display_number(item["value"], f"{where}.{side}.value", 0)
            if max(reveal["left"]["value"], reveal["right"]["value"]) == 0:
                raise SpecError(f"{where}: comparison needs at least one nonzero value")
        if kind == "trend":
            _require(reveal, ("points", "unit"), where)
            _text(reveal["unit"], f"{where}.unit", 8)
            points = reveal["points"]
            if not isinstance(points, list) or not 3 <= len(points) <= 6:
                raise SpecError(f"{where}.points: expected 3–6 points")
            for j, point in enumerate(points):
                loc = f"{where}.points[{j}]"
                if not isinstance(point, dict):
                    raise SpecError(f"{loc}: expected object")
                _require(point, ("label", "value"), loc)
                _text(point["label"], f"{loc}.label", 6)
                _display_number(point["value"], f"{loc}.value", 0)
            if max(point["value"] for point in points) == 0:
                raise SpecError(f"{where}: trend needs at least one nonzero value")
        end = start + ANIMATION_SECONDS[kind]
        if end > freeze_time(spec) + 1e-9:
            raise SpecError(f"{where}: animation ends at {end:.3f}s, past final-hold boundary {freeze_time(spec):.3f}s")
        interval = (start, end, where)
        if kind == "comparison":
            comparison_interval = interval
        if "sync_with" in reveal:
            synced_number = interval
        else:
            intervals.append(interval)
    if synced_number is not None:
        if comparison_interval is None or synced_number[0] != comparison_interval[0]:
            raise SpecError(f"{synced_number[2]}.sync_with: must share comparison start time")
        intervals.remove(comparison_interval)
        intervals.append((comparison_interval[0], max(comparison_interval[1], synced_number[1]),
                          comparison_interval[2] + " + synced number_roll"))
    if step_count > 3:
        raise SpecError("spec.reveals: process supports at most 3 steps")
    required = {"stat_highlight": "number_roll", "comparison": "comparison", "process": "step", "trend": "trend"}[layout]
    if required not in seen:
        raise SpecError(f"spec.reveals: {layout} requires {required}")
    if "title" not in seen:
        intervals.append((0.0, ANIMATION_SECONDS["title"], "implicit title"))
    intervals.sort()
    for previous, current in zip(intervals, intervals[1:]):
        if current[0] < previous[1] - 1e-9:
            raise SpecError(f"{current[2]}: animation time overlap with {previous[2]}")
    return spec


def load_spec(path: str | Path) -> dict:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            spec = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SpecError(f"cannot read spec {path}: {exc}") from exc
    return validate_spec(spec)
