import os
from pathlib import Path

from app import media


def test_packaged_media_tools_are_preferred_to_path(tmp_path, monkeypatch):
    tools_dir = tmp_path / "pcs-api" / "ffmpeg"
    tools_dir.mkdir(parents=True)
    suffix = ".exe" if os.name == "nt" else ""
    ffmpeg = tools_dir / f"ffmpeg{suffix}"
    ffprobe = tools_dir / f"ffprobe{suffix}"
    ffmpeg.write_bytes(b"ffmpeg")
    ffprobe.write_bytes(b"ffprobe")

    monkeypatch.setattr(media.sys, "frozen", True, raising=False)
    monkeypatch.setattr(media.sys, "executable", str(tmp_path / "pcs-api" / "pcs-api.exe"))
    monkeypatch.setattr(media.shutil, "which", lambda _name: None)

    assert media.executable("ffmpeg") == str(ffmpeg)
    assert media.executable("ffprobe") == str(ffprobe)


def test_development_media_tools_fall_back_to_path(tmp_path, monkeypatch):
    monkeypatch.setattr(media.sys, "frozen", False, raising=False)
    monkeypatch.setattr(media.shutil, "which", lambda name: f"/tools/{name}")

    assert media.executable("ffmpeg") == "/tools/ffmpeg"
    assert media.executable("ffprobe") == "/tools/ffprobe"


def test_packaged_media_tools_can_fall_back_to_path_when_not_bundled(tmp_path, monkeypatch):
    monkeypatch.setattr(media.sys, "frozen", True, raising=False)
    monkeypatch.setattr(media.sys, "executable", str(tmp_path / "pcs-api.exe"))
    monkeypatch.setattr(media.shutil, "which", lambda name: f"C:/tools/{name}.exe")

    assert media.executable("ffmpeg") == "C:/tools/ffmpeg.exe"
