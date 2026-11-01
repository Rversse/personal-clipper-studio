from pathlib import Path

from fastapi.testclient import TestClient

from app import database
from app import main as main_module
from app.main import app


def setup_test_storage(tmp_path, monkeypatch):
    media_dir = tmp_path / "media"
    thumbnail_dir = tmp_path / "thumbnails"
    monkeypatch.setattr(database, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(database, "THUMBNAIL_DIR", thumbnail_dir)
    monkeypatch.setattr(database, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setattr(main_module, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(main_module, "THUMBNAIL_DIR", thumbnail_dir)
    return media_dir, thumbnail_dir


def create_project(client):
    response = client.post("/api/projects", json={"name": "Import test"})
    assert response.status_code == 201
    return response.json()["id"]


def test_media_import_publishes_files_only_after_validation(tmp_path, monkeypatch):
    media_dir, thumbnail_dir = setup_test_storage(tmp_path, monkeypatch)
    observed = {}

    def fake_probe(path):
        observed["probe_saw_staged_file"] = path.name.startswith(".") and path.exists()
        observed["final_existed_during_probe"] = any(
            item for item in media_dir.glob("*.mp4")
        )
        return {
            "duration": 12.0, "width": 1920, "height": 1080, "fps": 30.0,
            "video_codec": "h264", "audio_codec": "aac",
        }

    def fake_thumbnail(source, destination):
        observed["thumbnail_saw_staged_source"] = source.exists()
        observed["thumbnail_was_staged"] = destination.name.startswith(".")
        destination.write_bytes(b"jpeg-thumbnail")
        return True

    monkeypatch.setattr(main_module, "probe_media", fake_probe)
    monkeypatch.setattr(main_module, "create_thumbnail", fake_thumbnail)

    with TestClient(app) as client:
        project_id = create_project(client)
        response = client.post(
            f"/api/projects/{project_id}/media",
            files={"file": ("sample.mp4", b"video-content", "video/mp4")},
        )

    assert response.status_code == 201
    payload = response.json()
    assert observed == {
        "probe_saw_staged_file": True,
        "final_existed_during_probe": False,
        "thumbnail_saw_staged_source": True,
        "thumbnail_was_staged": True,
    }
    assert (media_dir / f"{payload['id']}.mp4").read_bytes() == b"video-content"
    assert (thumbnail_dir / f"{payload['id']}.jpg").read_bytes() == b"jpeg-thumbnail"
    assert not list(media_dir.glob("*.uploading"))
    assert not list(thumbnail_dir.glob("*.uploading.jpg"))


def test_media_import_removes_staged_files_when_probe_rejects_upload(tmp_path, monkeypatch):
    media_dir, thumbnail_dir = setup_test_storage(tmp_path, monkeypatch)

    def reject_probe(_path):
        raise ValueError("FFprobe gagal membaca file: invalid media")

    monkeypatch.setattr(main_module, "probe_media", reject_probe)

    with TestClient(app) as client:
        project_id = create_project(client)
        response = client.post(
            f"/api/projects/{project_id}/media",
            files={"file": ("broken.mp4", b"not-a-video", "video/mp4")},
        )

    assert response.status_code == 422
    assert "FFprobe gagal" in response.json()["detail"]
    assert list(media_dir.iterdir()) == []
    assert list(thumbnail_dir.iterdir()) == []


def test_media_import_removes_staged_file_when_upload_exceeds_limit(tmp_path, monkeypatch):
    media_dir, thumbnail_dir = setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", 4)

    with TestClient(app) as client:
        project_id = create_project(client)
        response = client.post(
            f"/api/projects/{project_id}/media",
            files={"file": ("large.mp4", b"12345", "video/mp4")},
        )

    assert response.status_code == 413
    assert list(media_dir.iterdir()) == []
    assert list(thumbnail_dir.iterdir()) == []
