"""Align word cues to confirmed text; ASR wording never becomes caption wording."""
import hashlib
import math
import re
import unicodedata
from pathlib import Path
from opencc import OpenCC

_SIMPLIFY = OpenCC("t2s")


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def spoken_chars(text):
    result = []
    for index, char in enumerate(text):
        # Comparison only: rendered captions always retain the confirmed spelling.
        for normal in _SIMPLIFY.convert(unicodedata.normalize("NFKC", char)).lower():
            if unicodedata.category(normal)[0] in ("L", "N"):
                result.append((normal, index))
    return result


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def align_script(text, timing, minimum_match=0.75):
    if timing.get("schema_version") != 1 or timing.get("script_text") != text:
        raise ValueError("word timing must bind the exact confirmed script_text")
    if timing.get("method") not in ("edge_word_boundary", "asr_estimate"):
        raise ValueError("unsupported word timing method")
    if sha256(timing["source_path"]) != timing.get("source_sha256"):
        raise ValueError("word timing source audio hash changed")
    seconds = timing.get("source_seconds")
    if not _number(seconds) or seconds <= 0:
        raise ValueError("invalid word timing source duration")
    words = timing.get("words")
    if not isinstance(words, list) or not words:
        raise ValueError("missing word cues")
    heard = []
    previous_end = 0
    interpolated = False
    for word in words:
        start, end = word.get("start"), word.get("end")
        if (not _number(start) or not _number(end)
                or not 0 <= start < end <= seconds + .05
                or start < previous_end - .001
                or not isinstance(word.get("text"), str)):
            raise ValueError("word cues must be finite, ordered and within source audio")
        previous_end = end
        chars = spoken_chars(word["text"])
        interpolated |= len(chars) > 1
        for i, (char, _) in enumerate(chars):
            heard.append((char, start + (end-start)*i/len(chars),
                          start + (end-start)*(i+1)/len(chars)))
    script = spoken_chars(text)
    if not script or not heard or len(script) * len(heard) > 1_000_000:
        raise ValueError("empty or excessive spoken text")
    # Edit distance retains homophone substitutions as timing anchors, not new text.
    rows, cols = len(script), len(heard)
    distance = [list(range(cols + 1))]
    for i in range(1, rows + 1):
        row = [i]
        for j in range(1, cols + 1):
            row.append(min(distance[i-1][j-1] + (script[i-1][0] != heard[j-1][0]),
                           distance[i-1][j] + 1, row[j-1] + 1))
        distance.append(row)
    mapped, matched, substituted, missing = {}, 0, 0, []
    i, j = rows, cols
    while i or j:
        if i and j and distance[i][j] == distance[i-1][j-1] + (script[i-1][0] != heard[j-1][0]):
            index = script[i-1][1]
            mapped[index] = (heard[j-1][1], heard[j-1][2])
            if script[i-1][0] == heard[j-1][0]:
                matched += 1
            else:
                substituted += 1
            i -= 1
            j -= 1
        elif i and distance[i][j] == distance[i-1][j] + 1:
            missing.append(script[i-1][1])
            i -= 1
        else:
            j -= 1
    ratio = matched / len(script)
    heard_ratio = matched / len(heard)
    if ratio < minimum_match or heard_ratio < minimum_match:
        raise ValueError(f"word timing text match too low: {ratio:.3f}")
    return mapped, {"method": timing["method"], "exact_match_ratio": ratio,
                    "heard_match_ratio": heard_ratio,
                    "comparison_normalization": "NFKC, lowercase, OpenCC t2s; no output rewrite",
                    "substitutions": substituted, "missing_character_indices": sorted(missing),
                    "within_word_interpolation": interpolated,
                    "listening_verified": False}


def semantic_chunks(text, authored=None, maximum=22):
    chunks = authored if authored is not None else re.findall(r"[^，。！？；、,!?;]+[，。！？；、,!?;]*|[，。！？；、,!?;]+", text)
    if (not isinstance(chunks, list) or not chunks
            or any(not isinstance(chunk, str) or not chunk for chunk in chunks)
            or "".join(chunks) != text):
        raise ValueError("caption_chunks must preserve the exact confirmed text")
    if any(len(chunk) > maximum for chunk in chunks):
        raise ValueError("long caption needs authored semantic caption_chunks; refuse hard character cuts")
    return chunks


def aligned_events(narration, timing, index):
    text = narration.get("caption_text", narration["text"])
    if narration.get("audio_path") and sha256(narration["audio_path"]) != timing.get("source_sha256"):
        raise ValueError("caption timing is not bound to the played narration audio")
    points, evidence = align_script(text, timing)
    tempo = narration.get("caption_tempo", 1)
    start, end = narration["start"], narration["end"]
    if (not _number(tempo) or tempo <= 0 or not _number(start) or not _number(end)
            or not 0 <= start < end):
        raise ValueError("invalid caption playback timing")
    source_start = narration.get("caption_source_start", 0)
    if not _number(source_start) or source_start < 0:
        raise ValueError("invalid caption source trim")
    chunks = semantic_chunks(text, narration.get("caption_chunks"))
    layout = narration.get("caption_lines")
    if layout is not None:
        if (not isinstance(layout, list) or len(layout) != len(chunks)
                or any(not isinstance(lines, list) or not 1 <= len(lines) <= 2
                       or any(not isinstance(line, str) or not line or len(line) > 11 for line in lines)
                       or "".join(lines) != chunk for lines, chunk in zip(layout, chunks))):
            raise ValueError("caption_lines must preserve each chunk in at most two semantic lines of 11 characters")
    elif any(len(chunk) > 11 for chunk in chunks):
        raise ValueError("long caption needs authored semantic caption_lines")
    result, offset = [], 0
    for chunk_index, chunk in enumerate(chunks):
        spoken = [offset + i for _, i in spoken_chars(chunk)]
        if not spoken or spoken[0] not in points or spoken[-1] not in points:
            raise ValueError("caption boundary has missing spoken cue; needs alignment review")
        a = start + (points[spoken[0]][0] - source_start) / tempo
        b = start + (points[spoken[-1]][1] - source_start) / tempo
        if not start - .001 <= a < b <= end + .05:
            raise ValueError("caption cue does not fit actual narration slot")
        result.append({"narration_index": index, "start": max(start, a), "end": min(end, b),
                       "text": chunk, "display_text": "\n".join(layout[chunk_index]) if layout else chunk,
                       "timing_evidence": evidence})
        offset += len(chunk)
    return result


def edge_words(boundaries):
    """Convert Edge 100ns ticks; preserve the exact sidecar alongside the audio."""
    return [{"start": row["offset"] / 10_000_000,
             "end": (row["offset"] + row["duration"]) / 10_000_000,
             "text": row["text"]} for row in boundaries]
