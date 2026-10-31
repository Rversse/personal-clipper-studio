from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Callable


class RenderError(RuntimeError):
    pass


SUBTITLE_STYLES = {
    "clean": {"font": "Arial", "size": 52, "primary": "&H00FFFFFF", "outline": "&HCC000000", "bold": 0, "border": 2, "shadow": 1, "margin": 100},
    "bold": {"font": "Arial", "size": 62, "primary": "&H00FFFFFF", "outline": "&H00000000", "bold": 1, "border": 4, "shadow": 2, "margin": 110},
    "highlight": {"font": "Arial", "size": 58, "primary": "&H0000FFFF", "outline": "&HCC000000", "bold": 1, "border": 3, "shadow": 1, "margin": 115},
}


def _ass_timestamp(seconds: float) -> str:
    value = max(0, round(seconds * 100))
    hours, value = divmod(value, 360000)
    minutes, value = divmod(value, 6000)
    whole, fraction = divmod(value, 100)
    return "{}:{:02d}:{:02d}.{:02d}".format(hours, minutes, whole, fraction)


def _ass_escape(value: str) -> str:
    safe = "".join(char for char in value if char in "\n\t" or ord(char) >= 32)
    return safe.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\r\n", "\\N").replace("\n", "\\N").replace("\r", "\\N")


def build_ass_content(segments: list[dict], clip_start: float, clip_end: float, preset: str = "clean", aspect_ratio: str = "9:16") -> str:
    if preset not in SUBTITLE_STYLES or aspect_ratio not in OUTPUT_SIZES:
        raise RenderError("Preset atau rasio subtitle tidak didukung.")
    if not math.isfinite(clip_start) or not math.isfinite(clip_end) or clip_start < 0 or clip_end <= clip_start:
        raise RenderError("Rentang klip untuk subtitle tidak valid.")
    width, height = OUTPUT_SIZES[aspect_ratio]
    style = SUBTITLE_STYLES[preset]
    style_line = "Style: Default,{font},{size},{primary},&H000000FF,{outline},&H80000000,{bold},0,0,0,100,100,0,0,1,{border},{shadow},2,60,60,{margin},1".format(**style)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "WrapStyle: 2", "ScaledBorderAndShadow: yes",
        "PlayResX: {}".format(width), "PlayResY: {}".format(height), "", "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        style_line, "", "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for segment in segments:
        try:
            start, end, text = float(segment["start"]), float(segment["end"]), str(segment["text"])
        except (KeyError, TypeError, ValueError) as error:
            raise RenderError("Data segmen subtitle tidak valid.") from error
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise RenderError("Timestamp segmen subtitle tidak valid.")
        left, right = max(start, clip_start), min(end, clip_end)
        content = _ass_escape(text.strip())
        if right > left and content:
            lines.append("Dialogue: 0,{},{},Default,,0,0,0,,{}".format(_ass_timestamp(left - clip_start), _ass_timestamp(right - clip_start), content))
    return "\n".join(lines) + "\n"


def _escape_filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    for char in ("\\", ":", "'", ",", "[", "]"):
        value = value.replace(char, "\\" + char)
    return value


def subtitles_filter_available(ffmpeg: str | None = None) -> bool:
    executable = ffmpeg or shutil.which("ffmpeg")
    if not executable:
        return False
    try:
        result = subprocess.run([executable, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and " subtitles " in result.stdout


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
    subtitle_path: Path | None = None,
    subtitle_preset: str = "clean",
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
    if subtitle_path is not None:
        if subtitle_preset not in SUBTITLE_STYLES:
            raise RenderError("Preset subtitle tidak didukung.")
        style = SUBTITLE_STYLES[subtitle_preset]
        force_style = "FontName={font},FontSize={size},PrimaryColour={primary},OutlineColour={outline},Bold={bold},BorderStyle=1,Outline={border},Shadow={shadow},Alignment=2,MarginV={margin}".format(**style)
        video_filter += ",subtitles=filename='{}':force_style='{}'".format(_escape_filter_path(subtitle_path), force_style)
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
    subtitle_segments: list[dict] | None = None,
    subtitle_preset: str = "clean",
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
    subtitle_file: Path | None = None
    try:
        if subtitle_segments is not None:
            if not subtitles_filter_available(ffmpeg):
                raise RenderError("FFmpeg tidak memiliki filter subtitles/libass. Pasang build FFmpeg dengan dukungan libass atau nonaktifkan burn-in subtitle.")
            subtitle_file = destination.parent / ("." + destination.stem + "-" + uuid.uuid4().hex + ".ass")
            subtitle_file.write_text(build_ass_content(subtitle_segments, start_seconds, end_seconds, subtitle_preset, aspect_ratio), encoding="utf-8-sig")
        command = build_render_command(
            source, temporary_output, start_seconds, end_seconds, aspect_ratio, encoder, ffmpeg,
            subtitle_file, subtitle_preset
        )
        try:
            _execute(command, duration, progress)
        except RenderError:
            if encoder != "h264_nvenc":
                raise
            temporary_output.unlink(missing_ok=True)
            encoder = "libx264"
            cpu_command = build_render_command(
                source, temporary_output, start_seconds, end_seconds, aspect_ratio, encoder, ffmpeg,
                subtitle_file, subtitle_preset
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
        if subtitle_file is not None:
            subtitle_file.unlink(missing_ok=True)



def render_sequence(
    source: Path,
    destination: Path,
    segments: list[dict],
    aspect_ratio: str,
    progress: Callable[[float], None] | None = None,
    subtitle_segments: list[dict] | None = None,
    subtitle_preset: str = "clean",
) -> dict[str, str]:
    if not source.is_file():
        raise RenderError("File video sumber tidak ditemukan.")
    if not segments or len(segments) > 100:
        raise RenderError("Sequence harus berisi 1 sampai 100 klip.")
    if aspect_ratio not in OUTPUT_SIZES:
        raise RenderError("Rasio aspek tidak didukung.")
    normalized: list[tuple[float, float]] = []
    for segment in segments:
        try:
            start, end = float(segment["start"]), float(segment["end"])
        except (KeyError, TypeError, ValueError) as error:
            raise RenderError("Data klip sequence tidak valid.") from error
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise RenderError("Rentang salah satu klip tidak valid.")
        normalized.append((start, end))

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RenderError("FFmpeg belum terpasang atau tidak ditemukan di PATH.")
    if subtitle_segments is not None and not subtitles_filter_available(ffmpeg):
        raise RenderError("FFmpeg tidak memiliki filter subtitles/libass.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    part_paths = [
        destination.parent / (".{}-{}-{:03d}.mp4".format(destination.stem, token, index + 1))
        for index in range(len(normalized))
    ]
    concat_list = destination.parent / (".{}-{}.ffconcat".format(destination.stem, token))
    temporary_output = destination.with_name(destination.stem + ".partial" + destination.suffix)
    encoders: list[str] = []
    total_duration = sum(end - start for start, end in normalized)
    completed_duration = 0.0
    try:
        temporary_output.unlink(missing_ok=True)
        for index, ((start, end), part_path) in enumerate(zip(normalized, part_paths)):
            def segment_progress(value: float, *, base: float = completed_duration, length: float = end - start) -> None:
                if progress:
                    progress(min(0.98, (base + length * value) / total_duration * 0.95))
            result = render_clip(
                source, part_path, start, end, aspect_ratio,
                progress=segment_progress, subtitle_segments=subtitle_segments,
                subtitle_preset=subtitle_preset,
            )
            encoders.append(result["encoder"])
            completed_duration += end - start
        concat_entries = []
        for part_path in part_paths:
            safe_path = str(part_path.resolve()).replace("\\", "/").replace("'", "'\\''")
            concat_entries.append("file '{}'".format(safe_path))
        concat_list.write_text("\n".join(concat_entries) + "\n", encoding="utf-8")
        concat_command = [
            ffmpeg, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
        ]
        if len(set(encoders)) == 1:
            concat_command += ["-c", "copy"]
            final_encoder = encoders[0]
        else:
            concat_command += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k"]
            final_encoder = "libx264"
        concat_command += ["-movflags", "+faststart", str(temporary_output)]
        try:
            result = subprocess.run(
                concat_command, capture_output=True, text=True, timeout=6 * 60 * 60, check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RenderError("Penggabungan klip melewati batas waktu 6 jam.") from error
        except OSError as error:
            raise RenderError("Gagal menjalankan FFmpeg concat: {}".format(error)) from error
        if result.returncode != 0:
            raise RenderError((result.stderr or "FFmpeg gagal menggabungkan klip.")[-3000:])
        if not temporary_output.is_file() or temporary_output.stat().st_size < 1024:
            raise RenderError("Hasil gabungan tidak valid atau terlalu kecil.")
        os.replace(temporary_output, destination)
        if progress:
            progress(1.0)
        return {"encoder": final_encoder, "output": str(destination)}
    finally:
        temporary_output.unlink(missing_ok=True)
        concat_list.unlink(missing_ok=True)
        for part_path in part_paths:
            part_path.unlink(missing_ok=True)
