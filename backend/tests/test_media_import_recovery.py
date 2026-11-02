"""Recovery of files orphaned by a process crash during media import."""
from __future__ import annotations

import uuid

from app import database, main as main_module


def setup_storage(tmp_path, monkeypatch):
    media_dir = tmp_path / "media"
    thumbnail_dir = tmp_path / "thumbnails"
    monkeypatch.setattr(database, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(database, "THUMBNAIL_DIR", thumbnail_dir)
    monkeypatch.setattr(database, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "studio.sqlite3")
    monkeypatch.setattr(main_module, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(main_module, "THUMBNAIL_DIR", thumbnail_dir)
    database.ensure_storage()
    return media_dir, thumbnail_dir


def test_recovery_removes_only_uncommitted_import_artifacts(tmp_path, monkeypatch):
    media_dir, thumbnail_dir = setup_storage(tmp_path, monkeypatch)
    timestamp = main_module.now_iso()

    referenced_id = str(uuid.uuid4())
    orphan_id = str(uuid.uuid4())
    staged_id = str(uuid.uuid4())

    referenced_media = f"{referenced_id}.mp4"
    referenced_thumbnail = f"{referenced_id}.jpg"
    (media_dir / referenced_media).write_bytes(b"committed media")
    (thumbnail_dir / referenced_thumbnail).write_bytes(b"committed thumbnail")

    (media_dir / f"{orphan_id}.mp4").write_bytes(b"published before DB commit")
    (thumbnail_dir / f"{orphan_id}.jpg").write_bytes(b"orphan thumbnail")
    (media_dir / f".{staged_id}.uploading").write_bytes(b"partial upload")
    (thumbnail_dir / f".{staged_id}.uploading.jpg").write_bytes(b"partial thumbnail")

    # Names outside the importer's UUID namespace are never swept.
    (media_dir / "notes.txt").write_text("keep", encoding="utf-8")
    (thumbnail_dir / "custom-cover.jpg").write_bytes(b"keep")

    with database.get_connection() as connection:
        connection.execute(
            "INSERT INTO projects(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
            (str(uuid.uuid4()), "Recovery", "", timestamp, timestamp),
        )
        project_id = connection.execute(
            "SELECT id FROM projects WHERE name='Recovery'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO media_assets(
                id,project_id,original_name,stored_name,mime_type,file_size,duration,
                thumbnail_name,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                referenced_id, project_id, "source.mp4", referenced_media,
                "video/mp4", 14, 12.0, referenced_thumbnail, timestamp,
            ),
        )

    removed = main_module.recover_interrupted_media_imports()

    assert removed == {
        "staged_media": 1,
        "orphan_media": 1,
        "staged_thumbnails": 1,
        "orphan_thumbnails": 1,
    }
    assert (media_dir / referenced_media).read_bytes() == b"committed media"
    assert (thumbnail_dir / referenced_thumbnail).read_bytes() == b"committed thumbnail"
    assert (media_dir / "notes.txt").read_text(encoding="utf-8") == "keep"
    assert (thumbnail_dir / "custom-cover.jpg").read_bytes() == b"keep"
    assert sorted(path.name for path in media_dir.iterdir()) == [referenced_media, "notes.txt"]
    assert sorted(path.name for path in thumbnail_dir.iterdir()) == sorted([
        referenced_thumbnail, "custom-cover.jpg"
    ])


def test_recovery_is_idempotent_when_no_orphaned_import_files_remain(tmp_path, monkeypatch):
    setup_storage(tmp_path, monkeypatch)

    assert main_module.recover_interrupted_media_imports() == {
        "staged_media": 0,
        "orphan_media": 0,
        "staged_thumbnails": 0,
        "orphan_thumbnails": 0,
    }
