from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Any


def is_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe_file(
    path: Path,
    model_name: str = "small",
    language: str | None = None,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError(
            "Whisper belum terpasang. Jalankan: pip install -e \"backend[transcription]\""
        ) from error

    device = os.environ.get("PCS_WHISPER_DEVICE", "auto")
    compute_type = os.environ.get("PCS_WHISPER_COMPUTE_TYPE", "default")
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(path),
        language=language or None,
        vad_filter=True,
        word_timestamps=False,
    )

    results: list[dict[str, Any]] = []
    duration = float(info.duration or 0)
    for segment in segments:
        results.append({
            "start": round(float(segment.start), 3),
            "end": round(float(segment.end), 3),
            "text": segment.text.strip(),
        })
        if progress and duration > 0:
            progress(min(0.99, float(segment.end) / duration))

    if progress:
        progress(1.0)
    return {
        "language": info.language,
        "language_probability": float(info.language_probability or 0),
        "duration": duration,
        "segments": results,
    }
