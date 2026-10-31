from pathlib import Path
import uuid

from fastapi.testclient import TestClient

from app import database, rendering
from app.main import app


def setup_test_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(database, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(database, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "test.sqlite3")


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
    assert command[command.index("-map") + 2] == "0:a:0?"
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

    def fake_render(source, destination, start, end, ratio, progress=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"x" * 2048)
        if progress:
            progress(0.5)
        return {"encoder": "libx264", "output": str(destination)}

    monkeypatch.setattr(rendering, "render_clip", fake_render)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={"start_seconds": 10, "end_seconds": 40, "aspect_ratio": "9:16"},
        )
        assert response.status_code == 202
        job = response.json()
        assert job["status"] == "completed"
        assert job["progress"] == 1
        assert job["encoder"] == "libx264"
        assert client.get(f"/api/exports/{job['id']}").json()["id"] == job["id"]
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
