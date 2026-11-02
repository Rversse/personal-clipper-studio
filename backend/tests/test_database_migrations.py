"""Regression coverage for additive upgrades from legacy SQLite schemas."""
from __future__ import annotations

import sqlite3

from app import database


def configure_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(database, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(database, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "studio.sqlite3")


def create_legacy_database(path):
    """Create a representative pre-queue/pre-editor schema with real user data."""
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE projects (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE media_assets (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                original_name TEXT NOT NULL, stored_name TEXT NOT NULL,
                mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                file_size INTEGER NOT NULL, duration REAL, width INTEGER, height INTEGER,
                fps REAL, video_codec TEXT, audio_codec TEXT, thumbnail_name TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE transcription_jobs (
                id TEXT PRIMARY KEY,
                media_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'queued', model_name TEXT NOT NULL,
                language TEXT, detected_language TEXT, language_probability REAL,
                duration REAL, progress REAL NOT NULL DEFAULT 0, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE transcript_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES transcription_jobs(id) ON DELETE CASCADE,
                start REAL NOT NULL, end REAL NOT NULL, text TEXT NOT NULL
            );
            CREATE TABLE discovery_jobs (
                id TEXT PRIMARY KEY,
                media_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
                transcription_id TEXT NOT NULL REFERENCES transcription_jobs(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'queued', provider TEXT NOT NULL,
                model_name TEXT NOT NULL, mode TEXT NOT NULL, category TEXT,
                min_duration INTEGER NOT NULL, max_duration INTEGER NOT NULL,
                progress REAL NOT NULL DEFAULT 0, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE clip_candidates (
                id TEXT PRIMARY KEY,
                discovery_job_id TEXT NOT NULL REFERENCES discovery_jobs(id) ON DELETE CASCADE,
                start_seconds REAL NOT NULL, end_seconds REAL NOT NULL,
                title TEXT NOT NULL, hook TEXT NOT NULL, summary TEXT NOT NULL,
                rationale TEXT NOT NULL, score INTEGER NOT NULL,
                category TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE export_jobs (
                id TEXT PRIMARY KEY,
                media_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'queued',
                start_seconds REAL NOT NULL, end_seconds REAL NOT NULL,
                aspect_ratio TEXT NOT NULL, output_name TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );

            INSERT INTO projects VALUES('p-legacy','Legacy project','keep me','2024-01-01','2024-01-02');
            INSERT INTO media_assets(
                id,project_id,original_name,stored_name,file_size,created_at
            ) VALUES('m-legacy','p-legacy','original.mp4','m-legacy.mp4',12345,'2024-01-02');
            INSERT INTO transcription_jobs(
                id,media_id,status,model_name,created_at,updated_at
            ) VALUES('t-legacy','m-legacy','completed','small','2024-01-03','2024-01-03');
            INSERT INTO transcript_segments(job_id,start,end,text)
                VALUES('t-legacy',1.25,3.5,'legacy transcript survives');
            INSERT INTO discovery_jobs(
                id,media_id,transcription_id,status,provider,model_name,mode,
                min_duration,max_duration,created_at,updated_at
            ) VALUES('d-legacy','m-legacy','t-legacy','completed','ollama',
                    'legacy-model','auto',15,60,'2024-01-04','2024-01-04');
            INSERT INTO clip_candidates(
                id,discovery_job_id,start_seconds,end_seconds,title,hook,summary,
                rationale,score,category,created_at
            ) VALUES('c-legacy','d-legacy',1.25,25.0,'Legacy title','Legacy hook',
                    'Legacy summary','Legacy rationale',88,'education','2024-01-04');
            INSERT INTO export_jobs(
                id,media_id,status,start_seconds,end_seconds,aspect_ratio,
                output_name,created_at,updated_at
            ) VALUES('e-legacy','m-legacy','completed',1.25,25.0,'9:16',
                    'legacy-export.mp4','2024-01-05','2024-01-05');
            """
        )


def test_legacy_database_upgrade_preserves_user_data_and_adds_schema(tmp_path, monkeypatch):
    configure_database(tmp_path, monkeypatch)
    create_legacy_database(database.DATABASE_PATH)

    # Running initialization twice must not duplicate, reset, or damage legacy data.
    database.ensure_storage()
    database.ensure_storage()

    with database.get_connection() as connection:
        assert tuple(connection.execute(
            "SELECT name,description FROM projects WHERE id='p-legacy'"
        ).fetchone()) == ("Legacy project", "keep me")
        assert tuple(connection.execute(
            "SELECT original_name,file_size FROM media_assets WHERE id='m-legacy'"
        ).fetchone()) == ("original.mp4", 12345)
        assert tuple(connection.execute(
            "SELECT status,model_name FROM transcription_jobs WHERE id='t-legacy'"
        ).fetchone()) == ("completed", "small")
        assert tuple(connection.execute(
            "SELECT start,end,text FROM transcript_segments WHERE job_id='t-legacy'"
        ).fetchone()) == (1.25, 3.5, "legacy transcript survives")
        assert tuple(connection.execute(
            "SELECT status,provider FROM discovery_jobs WHERE id='d-legacy'"
        ).fetchone()) == ("completed", "ollama")
        assert tuple(connection.execute(
            "SELECT title,score FROM clip_candidates WHERE id='c-legacy'"
        ).fetchone()) == ("Legacy title", 88)
        assert tuple(connection.execute(
            "SELECT status,output_name FROM export_jobs WHERE id='e-legacy'"
        ).fetchone()) == ("completed", "legacy-export.mp4")

        expected_columns = {
            "transcription_jobs": {"attempt_count"},
            "discovery_jobs": {"attempt_count"},
            "clip_candidates": {
                "boundary_quality", "opening_boundary", "ending_boundary", "context_note"
            },
            "export_jobs": {
                "subtitle_payload", "subtitle_preset", "subtitle_style", "clip_segments",
                "crop_zoom", "crop_x", "crop_y", "render_settings"
            },
        }
        for table, expected in expected_columns.items():
            actual = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            assert expected <= actual

        assert tuple(connection.execute(
            "SELECT attempt_count FROM transcription_jobs WHERE id='t-legacy'"
        ).fetchone()[0] == 0
        assert tuple(connection.execute(
            "SELECT attempt_count FROM discovery_jobs WHERE id='d-legacy'"
        ).fetchone()[0] == 0
        assert tuple(connection.execute(
            "SELECT boundary_quality,context_note FROM clip_candidates WHERE id='c-legacy'"
        ).fetchone()) == (50, "")
        assert tuple(connection.execute(
            "SELECT subtitle_preset,crop_zoom,crop_x,crop_y FROM export_jobs WHERE id='e-legacy'"
        ).fetchone()) == ("clean", 1.0, 0.5, 0.5)

        assert tuple(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert tuple(connection.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 1
        assert tuple(connection.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0] == 1
        assert tuple(connection.execute("SELECT COUNT(*) FROM clip_candidates").fetchone()[0] == 1
        assert tuple(connection.execute("SELECT COUNT(*) FROM export_jobs").fetchone()[0] == 1
        assert tuple(connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert tuple(connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
