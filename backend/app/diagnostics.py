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

    return {
        "ffmpeg": _binary_info("ffmpeg"),
        "ffprobe": _binary_info("ffprobe"),
        "transcription": {
            "installed": transcription.is_available(),
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
