from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterator

APP_DATA_DIR = Path(os.environ.get("PCS_DATA_DIR", Path.home() / ".personal-clipper-studio")).expanduser()
MEDIA_DIR = APP_DATA_DIR / "media"
THUMBNAIL_DIR = APP_DATA_DIR / "thumbnails"
DATABASE_PATH = APP_DATA_DIR / "studio.sqlite3"


def ensure_storage() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection() as connection:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS media_assets (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                file_size INTEGER NOT NULL,
                duration REAL,
                width INTEGER,
                height INTEGER,
                fps REAL,
                video_codec TEXT,
                audio_codec TEXT,
                thumbnail_name TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_media_project ON media_assets(project_id);
            CREATE TABLE IF NOT EXISTS transcription_jobs (
                id TEXT PRIMARY KEY,
                media_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'queued',
                model_name TEXT NOT NULL,
                language TEXT,
                detected_language TEXT,
                language_probability REAL,
                duration REAL,
                progress REAL NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transcript_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES transcription_jobs(id) ON DELETE CASCADE,
                start REAL NOT NULL,
                end REAL NOT NULL,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_transcription_media ON transcription_jobs(media_id);
            CREATE INDEX IF NOT EXISTS idx_transcript_job ON transcript_segments(job_id);
            """
        )


from contextlib import contextmanager


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def connection_scope() -> Iterator[sqlite3.Connection]:
    with get_connection() as connection:
        yield connection
