from __future__ import annotations

import os
from pathlib import Path
import math
import shutil
from typing import Callable, Any

WHISPER_MODELS = {
    "tiny": 75 * 1024**2,
    "base": 145 * 1024**2,
    "small": 484 * 1024**2,
    "medium": 1530 * 1024**2,
    "large-v3": 3090 * 1024**2,
    "distil-large-v3": 1530 * 1024**2,
}
WHISPER_DISK_RESERVE_BYTES = 512 * 1024**2


def _whisper_cache_dir() -> Path:
    explicit_cache = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if explicit_cache:
        return Path(explicit_cache).expanduser()
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _available_disk_bytes(path: Path) -> int | None:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def whisper_model_preflight(model_name: str) -> dict[str, Any]:
    """Inspect the local Hugging Face cache and free space without downloading anything."""
    if model_name not in WHISPER_MODELS:
        raise ValueError("Model Whisper tidak dikenal.")
    cache_dir = _whisper_cache_dir()
    model_dir = cache_dir / f"models--Systran--faster-whisper-{model_name}"
    snapshots_dir = model_dir / "snapshots"
    downloaded = False
    try:
        downloaded = any(
            (snapshot / "model.bin").is_file()
            for snapshot in snapshots_dir.iterdir()
            if snapshot.is_dir()
        )
    except OSError:
        downloaded = False

    free_bytes = _available_disk_bytes(cache_dir)
    estimated_model_bytes = WHISPER_MODELS[model_name]
    required_bytes = 0 if downloaded else estimated_model_bytes + WHISPER_DISK_RESERVE_BYTES
    enough_space = True if downloaded or free_bytes is None else free_bytes >= required_bytes
    return {
        "model": model_name,
        "downloaded": downloaded,
        "cache_configured": cache_dir.exists(),
        "free_bytes": free_bytes,
        "estimated_download_bytes": 0 if downloaded else estimated_model_bytes,
        "required_free_bytes": required_bytes,
        "disk_space_ok": enough_space,
        "disk_space_known": free_bytes is not None,
    }


def is_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except (ImportError, OSError):
        # Windows native-extension load failures (for example a missing DLL)
        # are availability failures too; keep the API response controlled.
        return False


def transcribe_file(
    path: Path,
    model_name: str = "small",
    language: str | None = None,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    try:
        from faster_whisper import WhisperModel
    except (ImportError, OSError) as error:
        raise RuntimeError(
            "Whisper belum siap. Install backend[transcription] dan pastikan native runtime/DLL tersedia."
        ) from error

    preflight = whisper_model_preflight(model_name)
    if not preflight["disk_space_ok"]:
        required_mb = math.ceil(preflight["required_free_bytes"] / 1024**2)
        free_bytes = preflight["free_bytes"]
        free_mb = math.floor(free_bytes / 1024**2) if free_bytes is not None else 0
        raise RuntimeError(
            f"Ruang disk tidak cukup untuk model Whisper {model_name}. "
            f"Perlu sekitar {required_mb} MB ruang kosong; tersedia {free_mb} MB. "
            "Kosongkan ruang disk atau pilih model yang lebih kecil."
        )

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
        previous_word_end = segment_start
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
                    and segment_start <= previous_word_end <= word_start < word_end <= segment_end
                    and (probability is None or math.isfinite(probability) and 0 <= probability <= 1)
                ):
                    normalized_words.append({
                        "start": round(word_start, 3),
                        "end": round(word_end, 3),
                        "text": word_text,
                        "probability": probability,
                    })
                    previous_word_end = word_end
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
