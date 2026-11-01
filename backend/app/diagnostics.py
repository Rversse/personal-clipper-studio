from __future__ import annotations

import http.client
import ipaddress
import json
import os
import shutil
import subprocess
from typing import Any
from urllib.parse import urlsplit

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


def _ollama_status(endpoint: str, model: str) -> dict[str, Any]:
    """Check only a loopback Ollama instance; never send diagnostics to remote hosts."""
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname or ""
        port = parsed.port or 80
        loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return {
            "connection": "not_tested_unsafe_endpoint",
            "model_available": None,
            "models": [],
        }
    if (
        parsed.scheme != "http"
        or not loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return {
            "connection": "not_tested_unsafe_endpoint",
            "model_available": None,
            "models": [],
        }

    path = (parsed.path.rstrip("/") if parsed.path else "") + "/api/tags"
    connection = http.client.HTTPConnection(host, port, timeout=2)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Ollama returned a non-success status")
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Ollama response exceeded the diagnostics limit")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise ValueError("Invalid Ollama tags response")
        names = [
            str(item.get("name") or item.get("model") or "")
            for item in payload["models"]
            if isinstance(item, dict) and (item.get("name") or item.get("model"))
        ]
        normalized_model = model.strip()
        available = normalized_model in names or (
            ":" not in normalized_model and f"{normalized_model}:latest" in names
        )
        return {
            "connection": "connected",
            "model_available": available,
            "models": names[:50],
        }
    except (http.client.HTTPException, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return {
            "connection": "unreachable",
            "model_available": False,
            "models": [],
        }
    finally:
        connection.close()


def runtime_diagnostics() -> dict[str, Any]:
    provider = os.environ.get("PCS_AI_PROVIDER", "gemini").strip().lower()
    ollama = {
        "connection": "not_selected",
        "model_available": None,
        "models": [],
    }
    if provider == "gemini":
        model = os.environ.get("PCS_GEMINI_MODEL", "gemini-3.8-flash").strip()
        configured = bool(os.environ.get("PCS_GEMINI_API_KEY", "").strip() and model)
        ai_status = "configured" if configured else "missing_api_key"
    elif provider == "ollama":
        model = os.environ.get("PCS_OLLAMA_MODEL", "qwen3:8b").strip()
        endpoint = os.environ.get("PCS_OLLAMA_URL", "http://127.0.0.1:11434").strip()
        configured = bool(model and endpoint)
        ollama = _ollama_status(endpoint, model) if configured else ollama
        ai_status = (
            "connected_model_available"
            if ollama["connection"] == "connected" and ollama["model_available"]
            else "connected_model_missing"
            if ollama["connection"] == "connected"
            else "configured_not_connected"
            if configured
            else "invalid_configuration"
        )
    else:
        model = ""
        configured = False
        ai_status = "unsupported_provider"

    ffmpeg = _binary_info("ffmpeg")
    ffprobe = _binary_info("ffprobe")
    whisper_installed = transcription.is_available()
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
            "download_url": "https://github.com/Rversse/personal-clipper-studio/releases",
            "download_label": "Download / reinstall aplikasi",
            "install_note": "Jika runtime tidak terdeteksi pada installer desktop, perbaiki atau install ulang aplikasi. Model bahasa akan diunduh otomatis saat transkripsi pertama.",
        },
        {
            "id": "ollama",
            "name": "Ollama (opsional)",
            "required": False,
            "available": ollama_installed,
            "status": "installed" if ollama_installed else "missing_optional",
            "description": "Opsional untuk provider AI lokal. Pemeriksaan mendeteksi CLI; koneksi server dan model diperiksa terpisah hanya saat Ollama dipilih.",
            "download_url": "https://ollama.com/download/windows",
            "download_label": "Download Ollama untuk Windows",
            "install_note": "Setelah instalasi, jalankan Ollama dan unduh model yang dipilih. Tekan Check again untuk memeriksa server dan model.",
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
            "ollama_connection": ollama["connection"],
            "ollama_model_available": ollama["model_available"],
            "ollama_models": ollama["models"],
        },
    }


@router.get("/api/diagnostics")
async def get_runtime_diagnostics() -> dict[str, Any]:
    """Report local runtime readiness without exposing secrets or contacting remote providers."""
    return runtime_diagnostics()
