from fastapi.testclient import TestClient

from app import database
from app.main import app


def setup_test_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(database, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "test.sqlite3")


def test_health_and_project_crud(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        created = client.post("/api/projects", json={"name": "Podcast Test", "description": "Episode 1"})
        assert created.status_code == 201
        project = created.json()
        assert project["name"] == "Podcast Test"
        assert project["media_count"] == 0

        listed = client.get("/api/projects")
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert listed.json()[0]["id"] == project["id"]

        media = client.get(f"/api/projects/{project['id']}/media")
        assert media.status_code == 200
        assert media.json() == []


def test_project_validation(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.post("/api/projects", json={"name": "   "})
        assert response.status_code == 422
