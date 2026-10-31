from pathlib import Path
import uuid

from fastapi.testclient import TestClient

from app import database, rendering, main as main_module
from app.main import app


def setup_test_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(database, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(database, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setattr(main_module, "MEDIA_DIR", database.MEDIA_DIR)
    monkeypatch.setattr(main_module, "EXPORT_DIR", database.EXPORT_DIR)


def seed_media():
    project_id = str(uuid.uuid4())
    media_id = str(uuid.uuid4())
    timestamp = "2026-10-31T00:00:00+00:00"
    database.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    (database.MEDIA_DIR / "source.mp4").write_bytes(b"source-video")
    with database.get_connection() as connection:
        connection.execute(
            "INSERT INTO projects(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
            (project_id, "Export test", "", timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO media_assets(
                id,project_id,original_name,stored_name,mime_type,file_size,duration,created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (media_id, project_id, "source.mp4", "source.mp4", "video/mp4", 12, 120.0, timestamp),
        )
    return media_id


def test_render_command_has_trim_crop_and_safe_audio_mapping(tmp_path):
    command = rendering.build_render_command(
        tmp_path / "source.mp4", tmp_path / "output.mp4", 5, 35, "9:16", "libx264"
    )
    assert command[command.index("-ss") + 1] == "5.000"
    assert command[command.index("-t") + 1] == "30.000"
    assert "crop=1080:1920" in command[command.index("-vf") + 1]
    assert command[command.index("-map") + 3] == "0:a:0?"
    assert "libx264" in command
    assert "+faststart" in command


def test_render_command_rejects_invalid_range_and_ratio(tmp_path):
    for args in [
        (5, 5, "9:16"),
        (-1, 10, "9:16"),
        (0, 10, "4:3"),
    ]:
        try:
            rendering.build_render_command(
                tmp_path / "source.mp4", tmp_path / "output.mp4",
                args[0], args[1], args[2], "libx264"
            )
        except rendering.RenderError:
            pass
        else:
            raise AssertionError("Invalid render arguments must be rejected")


def test_export_api_persists_job_and_serves_completed_file(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")

    def fake_render(source, destination, start, end, ratio, progress=None, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"x" * 2048)
        if progress:
            progress(0.5)
        return {"encoder": "libx264", "output": str(destination)}

    monkeypatch.setattr(rendering, "render_clip", fake_render)
    real_run_export_job = main_module.run_export_job
    monkeypatch.setattr(main_module, "run_export_job", lambda *args: None)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={"start_seconds": 10, "end_seconds": 40, "aspect_ratio": "9:16"},
        )
        assert response.status_code == 202
        job = response.json()
        assert job["status"] == "queued"
        job_id = job["id"]
        real_run_export_job(
            job_id, str(database.MEDIA_DIR / "source.mp4"),
            str(database.EXPORT_DIR / job["output_name"])
        )
        job = client.get(f"/api/exports/{job_id}").json()
        assert job["status"] == "completed"
        assert job["progress"] == 1
        assert job["encoder"] == "libx264"
        assert job["id"] == job_id
        download = client.get(job["download_url"])
        assert download.status_code == 200
        assert len(download.content) == 2048
        assert client.get("/api/exports").json()[0]["id"] == job["id"]


def test_export_api_rejects_range_outside_source(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={"start_seconds": 90, "end_seconds": 130, "aspect_ratio": "9:16"},
        )
        assert response.status_code == 422
        assert "durasi video sumber" in response.json()["detail"]


def test_nvenc_runtime_failure_retries_with_cpu_and_replaces_atomically(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    destination = tmp_path / "final.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(rendering.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(rendering, "available_encoder", lambda _: "h264_nvenc")
    attempts = []

    def fake_execute(command, duration, progress):
        attempts.append(command)
        if "h264_nvenc" in command:
            raise rendering.RenderError("NVENC device unavailable")
        Path(command[-1]).write_bytes(b"x" * 2048)

    monkeypatch.setattr(rendering, "_execute", fake_execute)
    result = rendering.render_clip(source, destination, 0, 20, "16:9")
    assert result["encoder"] == "libx264"
    assert len(attempts) == 2
    assert destination.is_file()
    assert not (tmp_path / "final.partial.mp4").exists()



def test_ass_subtitles_are_clipped_and_escaped():
    content = rendering.build_ass_content(
        [
            {"start": 8.5, "end": 12, "text": "Hello {world}\nNext line"},
            {"start": 30, "end": 35, "text": "Outside clip"},
        ],
        10, 20, "bold", "9:16",
    )
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "Fontsize" in content
    assert "Dialogue: 0,0:00:00.00,0:00:02.00" in content
    assert r"\{world\}" in content
    assert r"\NNext line" in content
    assert "Outside clip" not in content


def test_ass_subtitles_reject_invalid_timestamps_and_preset():
    for segments, preset in [
        ([{"start": 5, "end": 5, "text": "bad"}], "clean"),
        ([{"start": float("nan"), "end": 8, "text": "bad"}], "clean"),
        ([], "unknown"),
    ]:
        try:
            rendering.build_ass_content(segments, 0, 10, preset)
        except rendering.RenderError:
            pass
        else:
            raise AssertionError("Invalid subtitle data must be rejected")


def test_render_command_adds_subtitle_filter(tmp_path):
    subtitle = tmp_path / "captions.ass"
    command = rendering.build_render_command(
        tmp_path / "source.mp4", tmp_path / "output.mp4", 0, 10,
        "9:16", "libx264", subtitle_path=subtitle, subtitle_preset="highlight",
    )
    video_filter = command[command.index("-vf") + 1]
    assert "subtitles=filename=" in video_filter
    assert "captions.ass" in video_filter
    assert "PrimaryColour=&H0000FFFF" in video_filter


def test_render_subtitle_requires_libass_and_cleans_temp_file(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    destination = tmp_path / "final.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(rendering.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(rendering, "available_encoder", lambda _: "libx264")
    monkeypatch.setattr(rendering, "subtitles_filter_available", lambda _: True)
    attempts = []

    def fake_execute(command, duration, progress):
        attempts.append(command)
        Path(command[-1]).write_bytes(b"x" * 2048)

    monkeypatch.setattr(rendering, "_execute", fake_execute)
    rendering.render_clip(
        source, destination, 0, 10, "9:16",
        subtitle_segments=[{"start": 1, "end": 3, "text": "Caption"}],
        subtitle_preset="clean",
    )
    assert len(attempts) == 1
    assert "subtitles=filename=" in attempts[0][attempts[0].index("-vf") + 1]
    assert not list(tmp_path.glob("*.ass"))
    assert not list(tmp_path.glob(".*.ass"))

    monkeypatch.setattr(rendering, "subtitles_filter_available", lambda _: False)
    try:
        rendering.render_clip(
            source, destination, 0, 10, "9:16",
            subtitle_segments=[{"start": 1, "end": 3, "text": "Caption"}],
        )
    except rendering.RenderError as error:
        assert "libass" in str(error)
    else:
        raise AssertionError("Missing libass must fail explicitly")
