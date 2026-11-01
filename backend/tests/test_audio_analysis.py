from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app import audio_analysis


def test_parse_audio_analysis_output_returns_rms_samples_and_silence_intervals():
    output = """
frame:0 pts:0 pts_time:0
lavfi.astats.Overall.RMS_level=-24.0
frame:1 pts:8000 pts_time:0.5
lavfi.astats.Overall.RMS_level=-18.0
[silencedetect @ 0x1] silence_start: 1.250
[silencedetect @ 0x1] silence_end: 1.750 | silence_duration: 0.500
"""

    result = audio_analysis.parse_audio_analysis_output(output, duration=2.0)

    assert result is not None
    assert result["energy_samples"] == [
        {"time": 0.25, "rms_db": -24.0},
        {"time": 0.75, "rms_db": -18.0},
    ]
    assert result["silences"] == [
        {"start": 1.25, "end": 1.75, "duration": 0.5}
    ]


def test_parse_audio_analysis_output_closes_trailing_silence_at_duration():
    output = "[silencedetect @ 0x1] silence_start: 3.5"

    result = audio_analysis.parse_audio_analysis_output(output, duration=4.0)

    assert result is not None
    assert result["silences"] == [
        {"start": 3.5, "end": 4.0, "duration": 0.5}
    ]


def test_analyze_audio_uses_ffmpeg_filter_and_returns_none_without_audio(tmp_path, monkeypatch):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    commands = []

    monkeypatch.setattr(audio_analysis, "executable", lambda name: "/usr/bin/ffmpeg")

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="Output file #0 does not contain any stream")

    monkeypatch.setattr(audio_analysis.subprocess, "run", fake_run)

    assert audio_analysis.analyze_audio(source, duration=12.0) is None
    assert commands
    assert "-map" in commands[0]
    assert "0:a:0" in commands[0]
    assert any("silencedetect" in value for value in commands[0])


def test_analyze_audio_handles_missing_ffmpeg(tmp_path, monkeypatch):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(audio_analysis, "executable", lambda name: None)

    assert audio_analysis.analyze_audio(source) is None



@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
def test_analyze_audio_smoke_with_real_ffmpeg(tmp_path):
    source = tmp_path / "speech-then-silence.wav"
    generate = subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono:d=1",
            "-filter_complex",
            "[0:a][1:a]concat=n=2:v=0:a=1[out]",
            "-map",
            "[out]",
            "-c:a",
            "pcm_s16le",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert generate.returncode == 0, generate.stderr

    result = audio_analysis.analyze_audio(source, duration=2.0, timeout=30)

    assert result is not None
    assert result["energy_samples"]
    assert result["silences"]
    assert any(abs(item["start"] - 1.0) <= 0.15 for item in result["silences"])
