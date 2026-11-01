from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from fastapi import APIRouter

from . import transcription

router = APIRouter()


def _binary_info(name: str) -> dict[str, Any]:
    executable = shutil.which(name)
    if not executable:
        return {"available": False, "version": None}
    try:
        result = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        first_line = (result.stdout or result.stderr).splitlines()
        return {
            "available": result.returncode == 0,
            "version": first_line[0][:180] if first_line else None,
        }
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "version": None}


def runtime_diagnostics() -> dict[str, Any]:
    provider = os.environ.get("PCS_AI_PROVIDER", "gemini").strip().lower()
    if provider == "gemini":
        model = os.environ.get("PCS_GEMINI_MODEL", "gemini-3.8-flash").strip()
        configured = bool(os.environ.get("PCS_GEMINI_API_KEY", "").strip() and model)
        ai_status = "configured" if configured else "missing_api_key"
    elif provider == "ollama":
        model = os.environ.get("PCS_OLLAMA_MODEL", "qwen3:8b").strip()
        endpoint = os.environ.get("PCS_OLLAMA_URL", "http://127.0.0.1:11434").strip()
        configured = bool(model and endpoint)
        ai_status = "configured_not_connection_tested" if configured else "invalid_configuration"
    else:
        model = ""
        configured = False
        ai_status = "unsupported_provider"

    ffmpeg = _binary_info("ffmpeg")
    ffprobe = _binary_info("ffprobe")
    whisper_installed = transcription.is_available()
    ollama_selected = provider == "ollama"
    ollama_installed = shutil.which("ollama") is not None
    dependencies = [
        {
            "id": "ffmpeg",
            "name": "FFmpeg + FFprobe",
            "required": True,
            "available": ffmpeg["available"] and ffprobe["available"],
            "status": "installed" if ffmpeg["available"] and ffprobe["available"] else "missing",
            "description": "Diperlukan untuk membaca metadata, membuat thumbnail, analisis audio, dan merender video.",
            "download_url": "https://www.gyan.dev/ffmpeg/builds/",
            "download_label": "Download FFmpeg untuk Windows",
            "install_note": "Install build Windows yang menyertakan ffmpeg.exe dan ffprobe.exe, lalu tambahkan folder bin ke PATH. Restart aplikasi setelah instalasi.",
        },
        {
            "id": "whisper",
            "name": "Whisper transcription runtime",
            "required": True,
            "available": whisper_installed,
            "status": "installed" if whisper_installed else "missing",
            "description": "Mesin transkripsi lokal faster-whisper. Pada installer Windows resmi, runtime ini sudah dibundel.",
            "download_url": "https://github.com/Rversse/personal-clipper-studio",
            "download_label": "Buka repository aplikasi",
            "install_note": "Jika runtime tidak terdeteksi pada installer desktop, perbaiki atau install ulang aplikasi. Model bahasa akan diunduh otomatis saat transkripsi pertama.",
        },
        {
            "id": "ollama",
            "name": "Ollama (opsional)",
            "required": False,
            "available": ollama_installed,
            "status": "installed" if ollama_installed else "missing_optional",
            "description": "Opsional untuk provider AI lokal. Pemeriksaan hanya mendeteksi program Ollama CLI; status server dan model belum diuji. Gemini tidak membutuhkan Ollama.",
            "download_url": "https://ollama.com/download/windows",
            "download_label": "Download Ollama untuk Windows",
            "install_note": "Setelah instalasi, jalankan Ollama dan unduh model yang dipilih. Koneksi server dan ketersediaan model belum diuji oleh pemeriksaan ini.",
        },
    ]

    return {
        "platform": os.name,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "dependencies": dependencies,
        "transcription": {
            "installed": whisper_installed,
            "device": os.environ.get("PCS_WHISPER_DEVICE", "auto"),
            "compute_type": os.environ.get("PCS_WHISPER_COMPUTE_TYPE", "default"),
            "model_downloaded": None,
        },
        "ai": {
            "provider": provider,
            "model": model or None,
            "configured": configured,
            "status": ai_status,
        },
    }


@router.get("/api/diagnostics")
async def get_runtime_diagnostics() -> dict[str, Any]:
    """Report local runtime readiness without exposing secrets or making provider calls."""
    return runtime_diagnostics()
