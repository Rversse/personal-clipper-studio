from __future__ import annotations

import os
from pathlib import Path
import math
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
        word_timestamps=True,
    )

    results: list[dict[str, Any]] = []
    duration = float(info.duration or 0)
    for segment in segments:
        segment_start = float(segment.start)
        segment_end = float(segment.end)
        normalized_words: list[dict[str, Any]] = []
        for word in getattr(segment, "words", None) or []:
            try:
                word_start = float(word.start)
                word_end = float(word.end)
                word_text = str(word.word or "").strip()
                raw_probability = getattr(word, "probability", None)
                probability = float(raw_probability) if raw_probability is not None else None
                if (
                    word_text
                    and math.isfinite(word_start)
                    and math.isfinite(word_end)
                    and segment_start <= word_start < word_end <= segment_end
                    and (probability is None or math.isfinite(probability) and 0 <= probability <= 1)
                ):
                    normalized_words.append({
                        "start": round(word_start, 3),
                        "end": round(word_end, 3),
                        "text": word_text,
                        "probability": probability,
                    })
            except (TypeError, ValueError, OverflowError):
                continue
        results.append({
            "start": round(segment_start, 3),
            "end": round(segment_end, 3),
            "text": segment.text.strip(),
            "words": normalized_words,
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
