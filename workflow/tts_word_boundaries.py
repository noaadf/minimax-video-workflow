"""Capture Edge word boundaries with the audio that produced them."""
import json
import os
import uuid
from pathlib import Path

from .media import duration
from .speech_timing import align_script, edge_words, sha256


async def save_with_timing(text, voice, audio_path, timing_path, communicator=None):
    audio, timing = Path(audio_path).resolve(), Path(timing_path).resolve()
    if audio == timing or audio.exists() or timing.exists():
        raise ValueError("timed speech needs distinct new output paths")
    audio.parent.mkdir(parents=True, exist_ok=True)
    timing.parent.mkdir(parents=True, exist_ok=True)
    if communicator is None:
        import edge_tts
        communicator = edge_tts.Communicate(text, voice.get("name", "zh-CN-YunjianNeural"),
            rate=voice.get("rate", "+0%"), pitch=voice.get("pitch", "-18Hz"),
            volume=voice.get("volume", "+0%"), boundary="WordBoundary")
    temp_audio = audio.with_name(uuid.uuid4().hex + ".part.mp3")
    temp_timing = timing.with_name(uuid.uuid4().hex + ".part.json")
    boundaries = []
    publishing = False
    try:
        with temp_audio.open("wb") as f:
            async for chunk in communicator.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    boundaries.append({field: chunk[field] for field in ("offset", "duration", "text")})
        if not temp_audio.stat().st_size:
            raise ValueError("timed speech stream returned no audio")
        evidence = {"schema_version": 1, "method": "edge_word_boundary", "script_text": text,
                    "source_path": str(temp_audio), "source_sha256": sha256(temp_audio),
                    "source_seconds": duration(temp_audio), "words": edge_words(boundaries),
                    "raw_boundaries": boundaries, "voice": voice, "listening_verified": False}
        align_script(text, evidence, minimum_match=1)
        evidence["source_path"] = str(audio)
        temp_timing.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Do not overwrite outputs created while the stream was in flight.
        # Keep the verified staging pair if publication needs diagnosis.
        publishing = True
        os.link(temp_audio, audio)
        os.link(temp_timing, timing)
        publishing = False
        return evidence
    finally:
        if not publishing:
            temp_audio.unlink(missing_ok=True)
            temp_timing.unlink(missing_ok=True)
