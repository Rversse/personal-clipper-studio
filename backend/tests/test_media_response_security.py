import os
import tempfile

# Isolate SQLite state before importing the application module.
os.environ.setdefault("PCS_DATA_DIR", tempfile.mkdtemp(prefix="pcs-media-security-tests-"))

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # app.database computes its storage paths at import time. Patch both the
    # module constants and the aliases imported by app.main so this test never
    # reads or writes a developer's real application data directory, even when
    # another test module imported app.database before this module was loaded.
    from app import database

    isolated_root = tmp_path / "pcs-test-data"
    isolated_media = isolated_root / "media"
    isolated_thumbnails = isolated_root / "thumbnails"
    isolated_exports = isolated_root / "exports"
    isolated_db = isolated_root / "studio.sqlite3"

    for name, value in {
        "APP_DATA_DIR": isolated_root,
        "MEDIA_DIR": isolated_media,
        "THUMBNAIL_DIR": isolated_thumbnails,
        "EXPORT_DIR": isolated_exports,
        "DATABASE_PATH": isolated_db,
    }.items():
        monkeypatch.setattr(database, name, value)
    monkeypatch.setattr(main, "MEDIA_DIR", isolated_media)
    monkeypatch.setattr(main, "THUMBNAIL_DIR", isolated_thumbnails)
    monkeypatch.setattr(main, "EXPORT_DIR", isolated_exports)

    monkeypatch.setattr(
        main,
        "probe_media",
        lambda _path: {
            "available": True,
            "duration": 1.0,
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "video_codec": "h264",
            "audio_codec": "aac",
        },
    )
    monkeypatch.setattr(main, "create_thumbnail", lambda _source, _destination: False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("extension", "expected_type"),
    [
        (".mp4", "video/mp4"),
        (".mov", "video/quicktime"),
        (".mkv", "video/x-matroska"),
        (".webm", "video/webm"),
        (".avi", "video/x-msvideo"),
        (".m4v", "video/mp4"),
        (".mpeg", "video/mpeg"),
        (".mpg", "video/mpeg"),
    ],
)
def test_uploaded_media_uses_server_controlled_mime_type(client, extension, expected_type):
    project = client.post(
        "/api/projects",
        json={"name": f"MIME security {extension}", "description": ""},
    )
    assert project.status_code == 201

    uploaded = client.post(
        f"/api/projects/{project.json()['id']}/media",
        files={"file": (f"sample{extension}", b"test media bytes", "text/html")},
    )
    assert uploaded.status_code == 201
    media = uploaded.json()

    assert media["mime_type"] == expected_type

    # Legacy databases may already contain client-controlled MIME metadata.
    from app.database import get_connection
    with get_connection() as connection:
        connection.execute(
            "UPDATE media_assets SET mime_type='text/html' WHERE id=?",
            (media["id"],),
        )

    response = client.get(media["preview_url"])
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == expected_type
    assert response.headers["x-content-type-options"] == "nosniff"


def test_export_download_sets_nosniff_header(client):
    # A completed export is served with a fixed video MIME type and download name.
    project = client.post(
        "/api/projects",
        json={"name": "Export header security", "description": ""},
    )
    assert project.status_code == 201

    uploaded = client.post(
        f"/api/projects/{project.json()['id']}/media",
        files={"file": ("sample.mp4", b"test media bytes", "text/html")},
    )
    assert uploaded.status_code == 201

    from app.database import EXPORT_DIR, get_connection
    import uuid
    from datetime import datetime, timezone

    job_id = str(uuid.uuid4())
    output_name = f"clip-{job_id}.mp4"
    (EXPORT_DIR / output_name).write_bytes(b"x" * 2048)
    timestamp = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO export_jobs(
                id,media_id,status,start_seconds,end_seconds,aspect_ratio,output_name,
                created_at,updated_at
            ) VALUES(?,?,'completed',0,1,'9:16',?,?,?)""",
            (job_id, uploaded.json()["id"], output_name, timestamp, timestamp),
        )

    response = client.get(f"/api/exports/{job_id}/file")
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == "video/mp4"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]
