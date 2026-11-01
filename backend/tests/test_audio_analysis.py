from __future__ import annotations

from types import SimpleNamespace

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
