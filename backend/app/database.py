from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

APP_DATA_DIR = Path(os.environ.get("PCS_DATA_DIR", Path.home() / ".personal-clipper-studio")).expanduser()
MEDIA_DIR = APP_DATA_DIR / "media"
THUMBNAIL_DIR = APP_DATA_DIR / "thumbnails"
EXPORT_DIR = APP_DATA_DIR / "exports"
DATABASE_PATH = APP_DATA_DIR / "studio.sqlite3"


def ensure_storage() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
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
            CREATE TABLE IF NOT EXISTS transcript_words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES transcription_jobs(id) ON DELETE CASCADE,
                segment_id INTEGER NOT NULL REFERENCES transcript_segments(id) ON DELETE CASCADE,
                word_index INTEGER NOT NULL,
                start REAL NOT NULL CHECK(start >= 0),
                end REAL NOT NULL CHECK(end > start),
                text TEXT NOT NULL,
                probability REAL CHECK(probability IS NULL OR (probability >= 0 AND probability <= 1)),
                UNIQUE(segment_id, word_index)
            );
            CREATE INDEX IF NOT EXISTS idx_transcript_words_job ON transcript_words(job_id, start);
            CREATE INDEX IF NOT EXISTS idx_transcript_words_job_segment
                ON transcript_words(job_id, segment_id, word_index);
            CREATE TABLE IF NOT EXISTS discovery_jobs (
                id TEXT PRIMARY KEY,
                media_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
                transcription_id TEXT NOT NULL REFERENCES transcription_jobs(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'queued',
                provider TEXT NOT NULL,
                model_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                category TEXT,
                min_duration INTEGER NOT NULL,
                max_duration INTEGER NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS clip_candidates (
                id TEXT PRIMARY KEY,
                discovery_job_id TEXT NOT NULL REFERENCES discovery_jobs(id) ON DELETE CASCADE,
                start_seconds REAL NOT NULL,
                end_seconds REAL NOT NULL,
                title TEXT NOT NULL,
                hook TEXT NOT NULL,
                summary TEXT NOT NULL,
                rationale TEXT NOT NULL,
                score INTEGER NOT NULL CHECK(score >= 0 AND score <= 100),
                category TEXT NOT NULL,
                boundary_quality INTEGER NOT NULL DEFAULT 50,
                opening_boundary TEXT NOT NULL DEFAULT 'Batas segmen transkrip',
                ending_boundary TEXT NOT NULL DEFAULT 'Batas segmen transkrip',
                context_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(discovery_job_id, start_seconds, end_seconds)
            );
            CREATE INDEX IF NOT EXISTS idx_discovery_media ON discovery_jobs(media_id);
            CREATE INDEX IF NOT EXISTS idx_candidate_job ON clip_candidates(discovery_job_id);
            CREATE TABLE IF NOT EXISTS export_jobs (
                id TEXT PRIMARY KEY,
                media_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'queued',
                start_seconds REAL NOT NULL,
                end_seconds REAL NOT NULL,
                aspect_ratio TEXT NOT NULL,
                output_name TEXT NOT NULL,
                subtitle_payload TEXT,
                subtitle_preset TEXT NOT NULL DEFAULT 'clean',
                subtitle_style TEXT,
                clip_segments TEXT,
                crop_zoom REAL NOT NULL DEFAULT 1.0,
                crop_x REAL NOT NULL DEFAULT 0.5,
                crop_y REAL NOT NULL DEFAULT 0.5,
                render_settings TEXT,
                encoder TEXT,
                progress REAL NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_export_media ON export_jobs(media_id);
            CREATE INDEX IF NOT EXISTS idx_export_status ON export_jobs(status);
            """
        )
        candidate_columns = {row["name"] for row in connection.execute("PRAGMA table_info(clip_candidates)").fetchall()}
        if "boundary_quality" not in candidate_columns:
            connection.execute("ALTER TABLE clip_candidates ADD COLUMN boundary_quality INTEGER NOT NULL DEFAULT 50")
        if "opening_boundary" not in candidate_columns:
            connection.execute("ALTER TABLE clip_candidates ADD COLUMN opening_boundary TEXT NOT NULL DEFAULT 'Batas segmen transkrip'")
        if "ending_boundary" not in candidate_columns:
            connection.execute("ALTER TABLE clip_candidates ADD COLUMN ending_boundary TEXT NOT NULL DEFAULT 'Batas segmen transkrip'")
        if "context_note" not in candidate_columns:
            connection.execute("ALTER TABLE clip_candidates ADD COLUMN context_note TEXT NOT NULL DEFAULT ''")
        export_columns = {row["name"] for row in connection.execute("PRAGMA table_info(export_jobs)").fetchall()}
        if "subtitle_payload" not in export_columns:
            connection.execute("ALTER TABLE export_jobs ADD COLUMN subtitle_payload TEXT")
        if "subtitle_preset" not in export_columns:
            connection.execute("ALTER TABLE export_jobs ADD COLUMN subtitle_preset TEXT NOT NULL DEFAULT 'clean'")
        if "subtitle_style" not in export_columns:
            connection.execute("ALTER TABLE export_jobs ADD COLUMN subtitle_style TEXT")
        if "clip_segments" not in export_columns:
            connection.execute("ALTER TABLE export_jobs ADD COLUMN clip_segments TEXT")
        if "crop_zoom" not in export_columns:
            connection.execute("ALTER TABLE export_jobs ADD COLUMN crop_zoom REAL NOT NULL DEFAULT 1.0")
        if "crop_x" not in export_columns:
            connection.execute("ALTER TABLE export_jobs ADD COLUMN crop_x REAL NOT NULL DEFAULT 0.5")
        if "crop_y" not in export_columns:
            connection.execute("ALTER TABLE export_jobs ADD COLUMN crop_y REAL NOT NULL DEFAULT 0.5")
        if "render_settings" not in export_columns:
            connection.execute("ALTER TABLE export_jobs ADD COLUMN render_settings TEXT")


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
