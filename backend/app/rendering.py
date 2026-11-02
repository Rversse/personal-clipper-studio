from __future__ import annotations

import json
import math
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable


class RenderError(RuntimeError):
    pass


class RenderCancelled(RenderError):
    """Raised when an export job is explicitly cancelled."""


class RenderInterrupted(RenderError):
    """Raised when application shutdown interrupts an export for later recovery."""


QUALITY_PROFILES = {
    "balanced": {"cpu_preset": "medium", "cpu_crf": "20", "nvenc_preset": "p5", "nvenc_cq": "23"},
    "high": {"cpu_preset": "slow", "cpu_crf": "18", "nvenc_preset": "p6", "nvenc_cq": "20"},
    "maximum": {"cpu_preset": "slower", "cpu_crf": "15", "nvenc_preset": "p7", "nvenc_cq": "17"},
}
FRAME_RATES = (24, 30, 60)


class RenderControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._interrupted = threading.Event()
        self._process: subprocess.Popen | None = None

    def _stop_process(self, force: bool = False) -> None:
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            if force:
                process.kill()
            else:
                process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def cancel(self) -> None:
        self._cancelled.set()
        self._stop_process()

    def interrupt(self) -> None:
        self._interrupted.set()
        self._stop_process()

    def check(self) -> None:
        if self._cancelled.is_set():
            raise RenderCancelled("Export dibatalkan oleh pengguna.")
        if self._interrupted.is_set():
            raise RenderInterrupted("Export dihentikan sementara karena aplikasi sedang ditutup.")

    def attach(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._process = process
            stopped = self._cancelled.is_set() or self._interrupted.is_set()
        if stopped and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def detach(self, process: subprocess.Popen) -> None:
        with self._lock:
            if self._process is process:
                self._process = None


_CONTROLS_LOCK = threading.Lock()
_CONTROLS: dict[str, RenderControl] = {}


def register_control(job_id: str) -> RenderControl:
    control = RenderControl()
    with _CONTROLS_LOCK:
        _CONTROLS[job_id] = control
    return control


def get_control(job_id: str) -> RenderControl | None:
    with _CONTROLS_LOCK:
        return _CONTROLS.get(job_id)


def unregister_control(job_id: str) -> None:
    with _CONTROLS_LOCK:
        _CONTROLS.pop(job_id, None)


def cancel_all_controls() -> int:
    """Cancel every render explicitly, preserving user-requested terminal semantics."""
    with _CONTROLS_LOCK:
        controls = list(_CONTROLS.values())
    for control in controls:
        control.cancel()
    return len(controls)


def interrupt_all_controls() -> int:
    """Interrupt active renders so their durable jobs can resume on next launch."""
    with _CONTROLS_LOCK:
        controls = list(_CONTROLS.values())
    for control in controls:
        control.interrupt()
    return len(controls)


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


def _subtitle_force_style(preset: str, aspect_ratio: str, options: dict | None = None) -> str:
    if preset not in SUBTITLE_STYLES or aspect_ratio not in OUTPUT_SIZES:
        raise RenderError("Preset atau rasio subtitle tidak didukung.")
    values = options or {}
    try:
        font_size = int(values.get("font_size", SUBTITLE_STYLES[preset]["size"]))
        vertical_position = int(values.get("vertical_position", 8))
        outline_width = float(values.get("outline_width", SUBTITLE_STYLES[preset]["border"]))
        background_opacity = int(values.get("background_opacity", 0))
    except (TypeError, ValueError, OverflowError) as error:
        raise RenderError("Pengaturan gaya subtitle tidak valid.") from error
    if (font_size < 24 or font_size > 120 or vertical_position < 5 or vertical_position > 30
            or not math.isfinite(outline_width) or outline_width < 0 or outline_width > 8
            or background_opacity < 0 or background_opacity > 90):
        raise RenderError("Pengaturan gaya subtitle berada di luar batas.")
    style = SUBTITLE_STYLES[preset]
    margin = round(OUTPUT_SIZES[aspect_ratio][1] * vertical_position / 100)
    if background_opacity:
        alpha = round(255 * (1 - background_opacity / 100))
        outline_colour = "&H{:02X}000000".format(alpha)
        border_style = 3
        outline_width = max(2.0, outline_width)
    else:
        outline_colour = style["outline"]
        border_style = 1
    return (
        "FontName={font},FontSize={size},PrimaryColour={primary},"
        "OutlineColour={outline},Bold={bold},BorderStyle={border_style},"
        "Outline={outline_width:.1f},Shadow={shadow},Alignment=2,MarginV={margin}"
    ).format(
        font=style["font"], size=font_size, primary=style["primary"],
        outline=outline_colour, bold=style["bold"], border_style=border_style,
        outline_width=outline_width, shadow=style["shadow"], margin=margin,
    )


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
    crop_zoom: float = 1.0,
    crop_x: float = 0.5,
    crop_y: float = 0.5,
    audio_volume: float = 1.0,
    subtitle_style: dict | None = None,
    quality_profile: str = "balanced",
    frame_rate: int | None = 30,
    normalize_audio: bool = False,
) -> list[str]:
    if aspect_ratio not in OUTPUT_SIZES:
        raise RenderError("Rasio aspek tidak didukung.")
    if not math.isfinite(start_seconds) or not math.isfinite(end_seconds):
        raise RenderError("Timestamp harus berupa angka yang valid.")
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise RenderError("Rentang klip tidak valid.")
    width, height = OUTPUT_SIZES[aspect_ratio]
    if not all(math.isfinite(value) for value in (crop_zoom, crop_x, crop_y)) or not 1.0 <= crop_zoom <= 3.0 or not 0.0 <= crop_x <= 1.0 or not 0.0 <= crop_y <= 1.0:
        raise RenderError("Pengaturan crop/zoom berada di luar batas.")
    if not math.isfinite(audio_volume) or not 0.0 <= audio_volume <= 2.0:
        raise RenderError("Volume audio harus berada pada rentang 0 sampai 2.")
    if quality_profile not in QUALITY_PROFILES:
        raise RenderError("Profil kualitas export tidak didukung.")
    if frame_rate is not None and frame_rate not in FRAME_RATES:
        raise RenderError("Frame rate export harus 24, 30, 60, atau mengikuti source.")
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop=w={width}/({crop_zoom:.4f}):h={height}/({crop_zoom:.4f}):"
        f"x=(iw-{width}/({crop_zoom:.4f}))*{crop_x:.4f}:"
        f"y=(ih-{height}/({crop_zoom:.4f}))*{crop_y:.4f},"
        f"scale={width}:{height},setsar=1"
    )
    if frame_rate is not None:
        video_filter += f",fps={frame_rate}"
    if subtitle_path is not None:
        if subtitle_preset not in SUBTITLE_STYLES:
            raise RenderError("Preset subtitle tidak didukung.")
        force_style = _subtitle_force_style(subtitle_preset, aspect_ratio, subtitle_style)
        video_filter += ",subtitles=filename='{}':force_style='{}'".format(_escape_filter_path(subtitle_path), force_style)
    command = [
        ffmpeg, "-hide_banner", "-y", "-i", str(source),
        "-ss", f"{start_seconds:.3f}", "-t", f"{end_seconds - start_seconds:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", video_filter, "-pix_fmt", "yuv420p",
    ]
    audio_filters = []
    if audio_volume != 1.0:
        audio_filters.append(f"volume={audio_volume:.4f}")
    if normalize_audio:
        audio_filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if audio_filters:
        command += ["-af", ",".join(audio_filters)]
    quality = QUALITY_PROFILES[quality_profile]
    if encoder == "h264_nvenc":
        command += ["-c:v", "h264_nvenc", "-preset", quality["nvenc_preset"], "-cq", quality["nvenc_cq"], "-b:v", "0"]
    elif encoder == "libx264":
        command += ["-c:v", "libx264", "-preset", quality["cpu_preset"], "-crf", quality["cpu_crf"]]
    else:
        raise RenderError("Video encoder tidak didukung.")
    command += [
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(destination),
    ]
    return command


def _execute(
    command: list[str],
    duration: float,
    progress: Callable[[float], None] | None,
    control: RenderControl | None = None,
) -> None:
    if control:
        control.check()
    process: subprocess.Popen | None = None
    reader: threading.Thread | None = None
    try:
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as error_log:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=error_log,
                text=True, encoding="utf-8", errors="replace",
            )
            if control:
                control.attach(process)
                control.check()
            if process.stdout is None:
                process.kill()
                raise RenderError("FFmpeg tidak menyediakan stream progress.")
            events: queue.Queue[str | None] = queue.Queue()

            def read_progress() -> None:
                try:
                    for output_line in process.stdout:
                        events.put(output_line)
                finally:
                    events.put(None)

            reader = threading.Thread(target=read_progress, name="ffmpeg-progress-reader", daemon=True)
            reader.start()
            deadline = time.monotonic() + 6 * 60 * 60
            last_progress = 0.0
            stream_finished = False
            while not stream_finished:
                if control:
                    control.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    raise RenderError("Rendering melewati batas waktu 6 jam.")
                try:
                    line = events.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    stream_finished = True
                    continue
                if progress and line.startswith("out_time_ms="):
                    try:
                        elapsed = max(0.0, int(line.partition("=")[2]) / 1_000_000)
                    except ValueError:
                        continue
                    current = min(0.99, max(last_progress, elapsed / duration))
                    if current > last_progress:
                        last_progress = current
                        progress(current)
            if control:
                control.check()
            try:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.wait()
                raise RenderError("Rendering melewati batas waktu 6 jam.") from error
            reader.join(timeout=2)
            if control:
                control.check()
            if process.returncode != 0:
                error_log.seek(0)
                detail = error_log.read()[-3000:]
                raise RenderError(detail.strip() or f"FFmpeg berhenti dengan kode {process.returncode}.")
    except OSError as error:
        if control and control._cancelled.is_set():
            raise RenderCancelled("Export dibatalkan oleh pengguna.") from error
        raise RenderError(f"Gagal menjalankan FFmpeg: {error}") from error
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if control:
                control.detach(process)
        if reader is not None:
            reader.join(timeout=2)


def validate_mp4_output(path: Path, expected_duration: float | None = None) -> dict:
    """Verify that an FFmpeg output is a readable MP4 with a real video stream."""
    if not path.is_file() or path.stat().st_size < 1024:
        raise RenderError("Hasil render tidak valid atau terlalu kecil.")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RenderError("FFprobe tidak ditemukan; hasil render tidak dapat diverifikasi.")
    try:
        probe = subprocess.run(
            [
                ffprobe, "-v", "error", "-show_entries",
                "format=format_name,duration:stream=codec_type,width,height",
                "-of", "json", str(path),
            ],
            check=False, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RenderError(f"Gagal memverifikasi hasil MP4 dengan FFprobe: {error}") from error
    if probe.returncode != 0:
        detail = (probe.stderr or "FFprobe menolak file output.").strip()
        raise RenderError(f"Hasil render bukan media yang valid: {detail[:500]}")
    try:
        metadata = json.loads(probe.stdout)
        container = str(metadata.get("format", {}).get("format_name", "")).lower().split(",")
        duration = float(metadata.get("format", {}).get("duration", 0))
        video = next(
            stream for stream in metadata.get("streams", [])
            if stream.get("codec_type") == "video"
        )
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
    except (ValueError, TypeError, KeyError, StopIteration, json.JSONDecodeError) as error:
        raise RenderError("Metadata hasil render tidak lengkap atau tidak dapat dibaca.") from error
    if not any(name in {"mp4", "mov"} for name in container):
        raise RenderError("Format container hasil render bukan MP4.")
    if not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0:
        raise RenderError("Hasil render tidak memiliki durasi atau stream video yang valid.")
    if expected_duration is not None:
        tolerance = max(0.5, expected_duration * 0.03)
        if not math.isfinite(expected_duration) or expected_duration <= 0 or abs(duration - expected_duration) > tolerance:
            raise RenderError("Durasi hasil render tidak sesuai dengan durasi klip yang diminta.")
    return {"duration": duration, "width": width, "height": height}


def render_clip(
    source: Path,
    destination: Path,
    start_seconds: float,
    end_seconds: float,
    aspect_ratio: str,
    progress: Callable[[float], None] | None = None,
    subtitle_segments: list[dict] | None = None,
    subtitle_preset: str = "clean",
    crop_zoom: float = 1.0,
    crop_x: float = 0.5,
    crop_y: float = 0.5,
    audio_volume: float = 1.0,
    subtitle_style: dict | None = None,
    control: RenderControl | None = None,
    quality_profile: str = "balanced",
    frame_rate: int | None = 30,
    normalize_audio: bool = False,
) -> dict[str, str]:
    if control:
        control.check()
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
            subtitle_file, subtitle_preset, crop_zoom, crop_x, crop_y, audio_volume, subtitle_style,
            quality_profile, frame_rate, normalize_audio,
        )
        try:
            _execute(command, duration, progress, control)
        except RenderError:
            if encoder != "h264_nvenc":
                raise
            temporary_output.unlink(missing_ok=True)
            encoder = "libx264"
            cpu_command = build_render_command(
                source, temporary_output, start_seconds, end_seconds, aspect_ratio, encoder, ffmpeg,
                subtitle_file, subtitle_preset, crop_zoom, crop_x, crop_y, audio_volume, subtitle_style,
                quality_profile, frame_rate, normalize_audio,
            )
            _execute(cpu_command, duration, progress, control)
        validate_mp4_output(temporary_output, expected_duration=duration)
        os.replace(temporary_output, destination)
        if progress:
            progress(1.0)
        return {"encoder": encoder, "output": str(destination)}
    finally:
        temporary_output.unlink(missing_ok=True)
        if subtitle_file is not None:
            subtitle_file.unlink(missing_ok=True)
        if control:
            control.check()



def render_sequence(
    source: Path,
    destination: Path,
    segments: list[dict],
    aspect_ratio: str,
    progress: Callable[[float], None] | None = None,
    subtitle_segments: list[dict] | None = None,
    subtitle_preset: str = "clean",
    crop_zoom: float = 1.0,
    crop_x: float = 0.5,
    crop_y: float = 0.5,
    audio_volume: float = 1.0,
    subtitle_style: dict | None = None,
    control: RenderControl | None = None,
    quality_profile: str = "balanced",
    frame_rate: int | None = 30,
    normalize_audio: bool = False,
) -> dict[str, str]:
    if control:
        control.check()
    if not source.is_file():
        raise RenderError("File video sumber tidak ditemukan.")
    if not segments or len(segments) > 100:
        raise RenderError("Sequence harus berisi 1 sampai 100 klip.")
    if aspect_ratio not in OUTPUT_SIZES:
        raise RenderError("Rasio aspek tidak didukung.")
    normalized: list[dict[str, float]] = []
    for segment in segments:
        try:
            start, end = float(segment["start"]), float(segment["end"])
            segment_zoom = float(segment.get("crop_zoom", crop_zoom))
            segment_x = float(segment.get("crop_x", crop_x))
            segment_y = float(segment.get("crop_y", crop_y))
            segment_audio_volume = float(segment.get("audio_volume", audio_volume))
        except (KeyError, TypeError, ValueError) as error:
            raise RenderError("Data klip sequence tidak valid.") from error
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise RenderError("Rentang salah satu klip tidak valid.")
        if (not all(math.isfinite(value) for value in (segment_zoom, segment_x, segment_y))
                or not 1.0 <= segment_zoom <= 3.0
                or not 0.0 <= segment_x <= 1.0 or not 0.0 <= segment_y <= 1.0):
            raise RenderError("Pengaturan crop salah satu klip tidak valid.")
        if not math.isfinite(segment_audio_volume) or not 0.0 <= segment_audio_volume <= 2.0:
            raise RenderError("Pengaturan volume salah satu klip tidak valid.")
        normalized.append({
            "start": start, "end": end,
            "crop_zoom": segment_zoom, "crop_x": segment_x, "crop_y": segment_y,
            "audio_volume": segment_audio_volume,
        })

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
    total_duration = sum(segment['end'] - segment['start'] for segment in normalized)
    completed_duration = 0.0
    try:
        temporary_output.unlink(missing_ok=True)
        for index, (segment, part_path) in enumerate(zip(normalized, part_paths)):
            start, end = segment['start'], segment['end']
            if control:
                control.check()
            def segment_progress(value: float, *, base: float = completed_duration, length: float = end - start) -> None:
                if progress:
                    progress(min(0.98, (base + length * value) / total_duration * 0.95))
            result = render_clip(
                source, part_path, start, end, aspect_ratio,
                progress=segment_progress, subtitle_segments=subtitle_segments,
                subtitle_preset=subtitle_preset,
                crop_zoom=segment['crop_zoom'], crop_x=segment['crop_x'], crop_y=segment['crop_y'],
                audio_volume=segment['audio_volume'], subtitle_style=subtitle_style, control=control,
                quality_profile=quality_profile, frame_rate=frame_rate, normalize_audio=normalize_audio,
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
            if control:
                control.check()
            concat_command = concat_command[:-1] + ["-progress", "pipe:1", "-nostats", concat_command[-1]]
            _execute(concat_command, total_duration, progress, control)
        except RenderCancelled:
            raise
        except RenderError as error:
            if "Rendering melewati batas waktu" in str(error):
                raise RenderError("Penggabungan klip melewati batas waktu 6 jam.") from error
            raise RenderError("FFmpeg concat gagal: {}".format(error)) from error
        validate_mp4_output(temporary_output, expected_duration=total_duration)
        if control:
            control.check()
        os.replace(temporary_output, destination)
        if progress:
            progress(1.0)
        return {"encoder": final_encoder, "output": str(destination)}
    finally:
        temporary_output.unlink(missing_ok=True)
        concat_list.unlink(missing_ok=True)
        for part_path in part_paths:
            part_path.unlink(missing_ok=True)
