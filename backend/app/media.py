from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def executable(name: str) -> str | None:
    return shutil.which(name)


def probe_media(path: Path) -> dict[str, Any]:
    ffprobe = executable("ffprobe")
    if not ffprobe:
        return {"available": False, "message": "FFprobe belum ditemukan di PATH."}

    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error", "-show_streams", "-show_format",
                "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("FFprobe melewati batas waktu analisis 90 detik.") from error
    if result.returncode != 0:
        detail = result.stderr.strip()[-1200:]
        raise ValueError(f"FFprobe gagal membaca file: {detail or 'format tidak dikenali'}")

    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    fmt = payload.get("format", {})

    raw_rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
    fps = None
    if raw_rate and raw_rate != "0/0":
        try:
            numerator, denominator = raw_rate.split("/", 1)
            fps = round(float(numerator) / float(denominator), 3) if float(denominator) else None
        except (ValueError, ZeroDivisionError):
            fps = None

    return {
        "available": True,
        "duration": float(fmt["duration"]) if fmt.get("duration") else None,
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": fps,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "format_name": fmt.get("format_name"),
        "bit_rate": int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
    }


def create_thumbnail(source: Path, destination: Path) -> bool:
    ffmpeg = executable("ffmpeg")
    if not ffmpeg:
        return False
    try:
        result = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", "1", "-i", str(source), "-frames:v", "1",
                "-vf", "scale=480:-2", str(destination),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        destination.unlink(missing_ok=True)
        return False
    return result.returncode == 0 and destination.exists() and destination.stat().st_size > 0
