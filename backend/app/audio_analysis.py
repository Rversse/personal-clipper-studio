from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Any

from .media import executable


_FRAME_TIME = re.compile(r"pts_time:([-+]?(?:\d+(?:\.\d*)?|\.\d+))")
_RMS_LEVEL = re.compile(
    r"lavfi\.astats\.Overall\.RMS_level=([-+]?(?:\d+(?:\.\d*)?|\.\d+)|-inf)",
    re.IGNORECASE,
)
_SILENCE_START = re.compile(r"silence_start:\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))")
_SILENCE_END = re.compile(
    r"silence_end:\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*\|\s*silence_duration:\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))"
)


def parse_audio_analysis_output(
    output: str,
    duration: float | None = None,
    frame_seconds: float = 0.5,
) -> dict[str, list[dict[str, float]]] | None:
    """Parse timestamped RMS metadata and silence intervals from FFmpeg logs."""
    energy_samples: list[dict[str, float]] = []
    silences: list[dict[str, float]] = []
    frame_time: float | None = None
    pending_silence: float | None = None

    for line in output.splitlines():
        time_match = _FRAME_TIME.search(line)
        if time_match:
            try:
                parsed_time = float(time_match.group(1))
                frame_time = parsed_time if math.isfinite(parsed_time) else None
            except (TypeError, ValueError, OverflowError):
                frame_time = None

        level_match = _RMS_LEVEL.search(line)
        if level_match and frame_time is not None:
            try:
                raw_level = level_match.group(1)
                level = -120.0 if raw_level.lower() == "-inf" else float(raw_level)
                if math.isfinite(level):
                    energy_samples.append({
                        "time": round(max(0.0, frame_time + frame_seconds / 2), 3),
                        "rms_db": round(level, 2),
                    })
            except (TypeError, ValueError, OverflowError):
                pass
            frame_time = None

        start_match = _SILENCE_START.search(line)
        if start_match:
            try:
                start = float(start_match.group(1))
                if math.isfinite(start) and start >= 0:
                    pending_silence = start
            except (TypeError, ValueError, OverflowError):
                pending_silence = None

        end_match = _SILENCE_END.search(line)
        if end_match and pending_silence is not None:
            try:
                end = float(end_match.group(1))
                silence_duration = float(end_match.group(2))
                if (
                    math.isfinite(end)
                    and math.isfinite(silence_duration)
                    and end > pending_silence
                    and silence_duration > 0
                ):
                    silences.append({
                        "start": round(pending_silence, 3),
                        "end": round(end, 3),
                        "duration": round(silence_duration, 3),
                    })
            except (TypeError, ValueError, OverflowError):
                pass
            pending_silence = None

    if (
        pending_silence is not None
        and duration is not None
        and math.isfinite(float(duration))
        and float(duration) > pending_silence
    ):
        silences.append({
            "start": round(pending_silence, 3),
            "end": round(float(duration), 3),
            "duration": round(float(duration) - pending_silence, 3),
        })

    if not energy_samples and not silences:
        return None
    return {"energy_samples": energy_samples, "silences": silences}


def analyze_audio(
    source: Path,
    duration: float | None = None,
    timeout: int = 900,
) -> dict[str, list[dict[str, float]]] | None:
    """Extract a lightweight RMS envelope and silence map with FFmpeg.

    Audio analysis is best-effort. Missing FFmpeg, silent/no-audio streams,
    unsupported codecs, or timeouts return None so transcript-only refinement
    can continue without blocking clip discovery.
    """
    ffmpeg = executable("ffmpeg")
    if not ffmpeg or not source.is_file():
        return None

    filter_graph = (
        "asetnsamples=n=8000:p=1,"
        "astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=RMS_level,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level,"
        "silencedetect=noise=-35dB:d=0.25"
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-af",
        filter_graph,
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None
    return parse_audio_analysis_output(
        result.stderr or result.stdout,
        duration=duration,
        frame_seconds=0.5,
    )
