from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable


class RenderError(RuntimeError):
    pass


OUTPUT_SIZES = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}


def available_encoder(ffmpeg: str | None = None) -> str:
    executable = ffmpeg or shutil.which("ffmpeg")
    if not executable:
        raise RenderError("FFmpeg belum terpasang atau tidak ditemukan di PATH.")
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RenderError(f"Tidak dapat memeriksa encoder FFmpeg: {error}") from error
    return "h264_nvenc" if result.returncode == 0 and "h264_nvenc" in result.stdout else "libx264"


def build_render_command(
    source: Path,
    destination: Path,
    start_seconds: float,
    end_seconds: float,
    aspect_ratio: str,
    encoder: str,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    if aspect_ratio not in OUTPUT_SIZES:
        raise RenderError("Rasio aspek tidak didukung.")
    if not math.isfinite(start_seconds) or not math.isfinite(end_seconds):
        raise RenderError("Timestamp harus berupa angka yang valid.")
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise RenderError("Rentang klip tidak valid.")
    width, height = OUTPUT_SIZES[aspect_ratio]
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )
    command = [
        ffmpeg, "-hide_banner", "-y", "-i", str(source),
        "-ss", f"{start_seconds:.3f}", "-t", f"{end_seconds - start_seconds:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", video_filter, "-pix_fmt", "yuv420p",
    ]
    if encoder == "h264_nvenc":
        command += ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23", "-b:v", "0"]
    elif encoder == "libx264":
        command += ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
    else:
        raise RenderError("Video encoder tidak didukung.")
    command += [
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(destination),
    ]
    return command


def _execute(command: list[str], duration: float, progress: Callable[[float], None] | None) -> None:
    try:
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as error_log:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=error_log,
                text=True, encoding="utf-8", errors="replace",
            )
            try:
                stdout, _ = process.communicate(timeout=6 * 60 * 60)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate()
                raise RenderError("Rendering melewati batas waktu 6 jam.") from error
            if process.returncode != 0:
                error_log.seek(0)
                detail = error_log.read()[-3000:]
                raise RenderError(detail.strip() or f"FFmpeg berhenti dengan kode {process.returncode}.")
        if progress:
            latest_time = 0.0
            for line in stdout.splitlines():
                if line.startswith("out_time_ms="):
                    try:
                        latest_time = max(latest_time, int(line.partition("=")[2]) / 1_000_000)
                    except ValueError:
                        continue
            progress(min(0.99, max(0.0, latest_time / duration)))
    except OSError as error:
        raise RenderError(f"Gagal menjalankan FFmpeg: {error}") from error


def render_clip(
    source: Path,
    destination: Path,
    start_seconds: float,
    end_seconds: float,
    aspect_ratio: str,
    progress: Callable[[float], None] | None = None,
) -> dict[str, str]:
    if not source.is_file():
        raise RenderError("File video sumber tidak ditemukan.")
    if aspect_ratio not in OUTPUT_SIZES:
        raise RenderError("Rasio aspek tidak didukung.")
    if not math.isfinite(start_seconds) or not math.isfinite(end_seconds):
        raise RenderError("Timestamp harus berupa angka yang valid.")
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise RenderError("Rentang klip tidak valid.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RenderError("FFmpeg belum terpasang atau tidak ditemukan di PATH.")
    encoder = available_encoder(ffmpeg)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = destination.with_name(destination.stem + ".partial" + destination.suffix)
    temporary_output.unlink(missing_ok=True)
    duration = end_seconds - start_seconds
    try:
        command = build_render_command(
            source, temporary_output, start_seconds, end_seconds, aspect_ratio, encoder, ffmpeg
        )
        try:
            _execute(command, duration, progress)
        except RenderError:
            if encoder != "h264_nvenc":
                raise
            temporary_output.unlink(missing_ok=True)
            encoder = "libx264"
            cpu_command = build_render_command(
                source, temporary_output, start_seconds, end_seconds, aspect_ratio, encoder, ffmpeg
            )
            _execute(cpu_command, duration, progress)
        if not temporary_output.is_file() or temporary_output.stat().st_size < 1024:
            raise RenderError("FFmpeg selesai tetapi file hasil tidak valid atau terlalu kecil.")
        os.replace(temporary_output, destination)
        if progress:
            progress(1.0)
        return {"encoder": encoder, "output": str(destination)}
    finally:
        temporary_output.unlink(missing_ok=True)
