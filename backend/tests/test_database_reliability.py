"""SQLite durability, migration idempotence, and referential-integrity checks."""
from __future__ import annotations

import sqlite3

import pytest

from app import database


def configure_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(database, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(database, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "studio.sqlite3")


def test_storage_initialization_is_idempotent_and_database_remains_healthy(tmp_path, monkeypatch):
    configure_database(tmp_path, monkeypatch)
    database.ensure_storage()
    database.ensure_storage()

    with database.get_connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        indexes = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert {
            "idx_transcription_queue", "idx_discovery_queue",
            "idx_media_project", "idx_export_status",
        } <= indexes

        for table in ("transcription_jobs", "discovery_jobs"):
            columns = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            assert "attempt_count" in columns


def test_foreign_keys_are_enforced_on_every_connection(tmp_path, monkeypatch):
    configure_database(tmp_path, monkeypatch)
    database.ensure_storage()

    with database.get_connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO media_assets(
                    id,project_id,original_name,stored_name,file_size,created_at
                ) VALUES('orphan-media','missing-project','orphan.mp4','orphan.mp4',1,'now')"""
            )

    with database.get_connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 0


def test_project_deletion_cascades_through_media_and_processing_history(tmp_path, monkeypatch):
    configure_database(tmp_path, monkeypatch)
    database.ensure_storage()

    with database.get_connection() as connection:
        connection.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES('project-1','Test','now','now')"
        )
        connection.execute(
            """INSERT INTO media_assets(
                id,project_id,original_name,stored_name,file_size,created_at
            ) VALUES('media-1','project-1','source.mp4','media-1.mp4',10,'now')"""
        )
        connection.execute(
            """INSERT INTO transcription_jobs(
                id,media_id,model_name,created_at,updated_at
            ) VALUES('transcript-1','media-1','tiny','now','now')"""
        )
        connection.execute(
            "INSERT INTO transcript_segments(job_id,start,end,text) VALUES('transcript-1',0,2,'Hello')"
        )
        connection.execute(
            """INSERT INTO transcript_words(job_id,segment_id,word_index,start,end,text)
               VALUES('transcript-1',1,0,0,1,'Hello')"""
        )
        connection.execute(
            """INSERT INTO discovery_jobs(
                id,media_id,transcription_id,provider,model_name,mode,
                min_duration,max_duration,created_at,updated_at
            ) VALUES('discovery-1','media-1','transcript-1','gemini','test','auto',15,60,'now','now')"""
        )
        connection.execute(
            """INSERT INTO clip_candidates(
                id,discovery_job_id,start_seconds,end_seconds,title,hook,summary,
                rationale,score,category,created_at
            ) VALUES('candidate-1','discovery-1',0,20,'Title','Hook','Summary',
                    'Reason',90,'education','now')"""
        )
        connection.execute(
            """INSERT INTO export_jobs(
                id,media_id,start_seconds,end_seconds,aspect_ratio,output_name,
                created_at,updated_at
            ) VALUES('export-1','media-1',0,20,'9:16','clip.mp4','now','now')"""
        )

        connection.execute("DELETE FROM projects WHERE id='project-1'")
        for table in (
            "media_assets", "transcription_jobs", "transcript_segments",
            "transcript_words", "discovery_jobs", "clip_candidates", "export_jobs",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
