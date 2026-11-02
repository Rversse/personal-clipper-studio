from __future__ import annotations

import sys
import time
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import database, main as main_module, transcription
from app.main import app


def setup_test_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(database, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(main_module, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(main_module, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "test.sqlite3")


def wait_for_transcription(client, job_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/transcriptions/{job_id}")
        assert response.status_code == 200
        latest = response.json()
        if latest["status"] in {"completed", "failed", "cancelled"}:
            return latest
        time.sleep(0.02)
    raise AssertionError(f"Transcription job did not finish; last state: {latest}")


def seed_media():
    project_id = str(uuid.uuid4())
    media_id = str(uuid.uuid4())
    timestamp = "2026-10-31T00:00:00+00:00"
    source = database.MEDIA_DIR / f"{media_id}.mp4"
    source.write_bytes(b"fake-video")
    with database.get_connection() as connection:
        connection.execute(
            "INSERT INTO projects(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
            (project_id, "Test", "", timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO media_assets(
                id,project_id,original_name,stored_name,mime_type,file_size,created_at
            ) VALUES(?,?,?,?,?,?,?)""",
            (media_id, project_id, "sample.mp4", source.name, "video/mp4", 10, timestamp),
        )
    return media_id



def test_queued_transcription_can_be_cancelled(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "start_job_workers", lambda: None)
    with TestClient(app) as client:
        media_id = seed_media()
        job_id = str(uuid.uuid4())
        timestamp = main_module.now_iso()
        with database.get_connection() as connection:
            connection.execute(
                """INSERT INTO transcription_jobs(
                    id,media_id,status,model_name,created_at,updated_at
                ) VALUES(?,?,'queued','tiny',?,?)""",
                (job_id, media_id, timestamp, timestamp),
            )
        response = client.post(f"/api/transcriptions/{job_id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert "dibatalkan" in response.json()["error"]



def test_active_transcription_discards_results_after_cooperative_cancel(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "start_job_workers", lambda: None)
    with TestClient(app):
        media_id = seed_media()
        job_id = str(uuid.uuid4())
        timestamp = main_module.now_iso()
        with database.get_connection() as connection:
            connection.execute(
                """INSERT INTO transcription_jobs(
                    id,media_id,status,model_name,created_at,updated_at
                ) VALUES(?,?,'queued','tiny',?,?)""",
                (job_id, media_id, timestamp, timestamp),
            )

        def fake_transcribe(path, model_name, language, progress, cancel_check=None):
            assert main_module._JOB_CANCEL_EVENTS[job_id].is_set() is False
            main_module._JOB_CANCEL_EVENTS[job_id].set()
            cancel_check()
            raise AssertionError("Cancellation must stop result persistence.")

        monkeypatch.setattr(transcription, "transcribe_file", fake_transcribe)
        source = database.MEDIA_DIR / f"{media_id}.mp4"
        main_module.run_transcription_job(job_id, str(source), "tiny", "id")

        with database.get_connection() as connection:
            job = connection.execute(
                "SELECT status,error FROM transcription_jobs WHERE id=?", (job_id,)
            ).fetchone()
            segments = connection.execute(
                "SELECT COUNT(*) AS count FROM transcript_segments WHERE job_id=?", (job_id,)
            ).fetchone()["count"]
        assert job["status"] == "cancelled"
        assert "dibatalkan" in job["error"]
        assert segments == 0


def test_duplicate_active_transcription_request_reuses_existing_job(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "start_job_workers", lambda: None)
    monkeypatch.setattr(transcription, "is_available", lambda: True)
    with TestClient(app) as client:
        media_id = seed_media()
        payload = {"model_name": "tiny", "language": "id"}
        first = client.post(f"/api/media/{media_id}/transcription", json=payload)
        second = client.post(f"/api/media/{media_id}/transcription", json=payload)
        assert first.status_code == second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        with database.get_connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM transcription_jobs WHERE media_id=?",
                (media_id,),
            ).fetchone()["count"]
        assert count == 1


def test_shutdown_keeps_queued_jobs_and_requeues_processing_jobs(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "start_job_workers", lambda: None)
    with TestClient(app):
        media_id = seed_media()
        timestamp = main_module.now_iso()
        queued_id = str(uuid.uuid4())
        processing_id = str(uuid.uuid4())
        with database.get_connection() as connection:
            connection.executemany(
                """INSERT INTO transcription_jobs(
                    id,media_id,status,model_name,created_at,updated_at
                ) VALUES(?,? ,?,'tiny',?,?)""",
                [
                    (queued_id, media_id, "queued", timestamp, timestamp),
                    (processing_id, media_id, "processing", timestamp, timestamp),
                ],
            )

        main_module.stop_job_workers()

        with database.get_connection() as connection:
            states = {
                row["id"]: (row["status"], row["attempt_count"])
                for row in connection.execute(
                    "SELECT id,status,attempt_count FROM transcription_jobs WHERE id IN (?,?)",
                    (queued_id, processing_id),
                ).fetchall()
            }
        assert states == {queued_id: ("queued", 0), processing_id: ("queued", 0)}


def test_recovery_requeues_interrupted_jobs_and_enforces_attempt_limit(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    database.ensure_storage()
    media_id = seed_media()
    timestamp = main_module.now_iso()
    completed_transcript = str(uuid.uuid4())
    retry_transcription = str(uuid.uuid4())
    exhausted_transcription = str(uuid.uuid4())
    retry_discovery = str(uuid.uuid4())
    exhausted_discovery = str(uuid.uuid4())
    with database.get_connection() as connection:
        connection.executemany(
            """INSERT INTO transcription_jobs(
                id,media_id,status,model_name,progress,attempt_count,created_at,updated_at
            ) VALUES(?,?,?,'tiny',0.7,?,?,?)""",
            [
                (completed_transcript, media_id, "completed", 0, timestamp, timestamp),
                (retry_transcription, media_id, "processing", 1, timestamp, timestamp),
                (exhausted_transcription, media_id, "processing", main_module.MAX_JOB_ATTEMPTS, timestamp, timestamp),
            ],
        )
        connection.executemany(
            """INSERT INTO discovery_jobs(
                id,media_id,transcription_id,status,provider,model_name,mode,
                min_duration,max_duration,progress,attempt_count,created_at,updated_at
            ) VALUES(?,?,?,'processing','test','test-model','auto',15,60,0.6,?,?,?)""",
            [
                (retry_discovery, media_id, completed_transcript, 1, timestamp, timestamp),
                (exhausted_discovery, media_id, completed_transcript, main_module.MAX_JOB_ATTEMPTS, timestamp, timestamp),
            ],
        )

    main_module._recover_interrupted_jobs()

    with database.get_connection() as connection:
        transcription_states = {
            row["id"]: (row["status"], row["progress"], row["attempt_count"])
            for row in connection.execute(
                "SELECT id,status,progress,attempt_count FROM transcription_jobs"
            ).fetchall()
        }
        discovery_states = {
            row["id"]: (row["status"], row["progress"], row["attempt_count"])
            for row in connection.execute(
                "SELECT id,status,progress,attempt_count FROM discovery_jobs"
            ).fetchall()
        }
    assert transcription_states[retry_transcription] == ("queued", 0.0, 1)
    assert transcription_states[exhausted_transcription][0] == "failed"
    assert discovery_states[retry_discovery] == ("queued", 0.0, 1)
    assert discovery_states[exhausted_discovery][0] == "failed"


def test_transcription_requires_optional_whisper(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(transcription, "is_available", lambda: False)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(f"/api/media/{media_id}/transcription", json={})
        assert response.status_code == 503
        assert "Whisper belum terpasang" in response.json()["detail"]


def test_transcription_job_persists_segments(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(transcription, "is_available", lambda: True)

    def fake_transcribe(path, model_name, language, progress, cancel_check=None):
        assert path.is_file()
        assert model_name == "tiny"
        assert language == "id"
        progress(0.5)
        return {
            "language": "id",
            "language_probability": 0.98,
            "duration": 5.0,
            "segments": [{"start": 0.2, "end": 2.4, "text": "Halo semuanya."}],
        }

    monkeypatch.setattr(transcription, "transcribe_file", fake_transcribe)
    with TestClient(app) as client:
        media_id = seed_media()
        created = client.post(
            f"/api/media/{media_id}/transcription",
            json={"model_name": "tiny", "language": "id"},
        )
        assert created.status_code == 202
        job_id = created.json()["id"]

        payload = wait_for_transcription(client, job_id)
        assert payload["status"] == "completed"
        assert payload["detected_language"] == "id"
        assert payload["progress"] == 1
        assert payload["segments"] == [
            {"id": payload["segments"][0]["id"], "start": 0.2, "end": 2.4, "text": "Halo semuanya.", "words": []}
        ]



def test_word_timestamps_are_persisted_and_returned(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(transcription, "is_available", lambda: True)

    def fake_transcribe(path, model_name, language, progress, cancel_check=None):
        return {
            "language": "id", "language_probability": 0.99, "duration": 4.0,
            "segments": [{
                "start": 0.2, "end": 2.4, "text": "Halo dunia.",
                "words": [
                    {"start": 0.2, "end": 0.8, "text": "Halo", "probability": 0.98},
                    {"start": 0.9, "end": 1.4, "text": "dunia.", "probability": 0.95},
                    {"start": 1.5, "end": 3.0, "text": "invalid", "probability": 0.8},
                ],
            }],
        }

    monkeypatch.setattr(transcription, "transcribe_file", fake_transcribe)
    with TestClient(app) as client:
        media_id = seed_media()
        created = client.post(
            f"/api/media/{media_id}/transcription",
            json={"model_name": "tiny", "language": "id"},
        )
        job_id = created.json()["id"]
        payload = wait_for_transcription(client, job_id)
        assert len(payload["segments"]) == 1
        assert payload["segments"][0]["words"] == [
            {"start": 0.2, "end": 0.8, "text": "Halo", "probability": 0.98},
            {"start": 0.9, "end": 1.4, "text": "dunia.", "probability": 0.95},
        ]


def test_legacy_transcript_segments_have_empty_words(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    with TestClient(app) as client:
        media_id = seed_media()
        timestamp = main_module.now_iso()
        with database.get_connection() as connection:
            job_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO transcription_jobs(id,media_id,status,model_name,created_at,updated_at)
                   VALUES(?,?,'completed','tiny',?,?)""",
                (job_id, media_id, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO transcript_segments(job_id,start,end,text) VALUES(?,?,?,?)",
                (job_id, 0.0, 1.8, "Legacy transcript"),
            )
        response = client.get(f"/api/transcriptions/{job_id}")
        assert response.status_code == 200
        assert response.json()["segments"][0]["text"] == "Legacy transcript"
        assert response.json()["segments"][0]["words"] == []


def test_update_word_timing_validates_and_persists(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(transcription, "is_available", lambda: True)

    def fake_transcribe(path, model_name, language, progress, cancel_check=None):
        return {
            "language": "id", "language_probability": 0.9, "duration": 3.0,
            "segments": [{
                "start": 0.0, "end": 2.0, "text": "satu dua",
                "words": [
                    {"start": 0.1, "end": 0.5, "text": "satu", "probability": 0.9},
                    {"start": 0.6, "end": 1.0, "text": "dua", "probability": 0.8},
                ],
            }],
        }

    monkeypatch.setattr(transcription, "transcribe_file", fake_transcribe)
    with TestClient(app) as client:
        media_id = seed_media()
        created = client.post(
            f"/api/media/{media_id}/transcription",
            json={"model_name": "tiny"},
        )
        job_id = created.json()["id"]
        segment = wait_for_transcription(client, job_id)["segments"][0]
        segment_id = segment["id"]
        good = client.patch(
            f"/api/transcriptions/{job_id}/segments/{segment_id}/words/0",
            json={"start": 0.12, "end": 0.55, "text": "satu"},
        )
        assert good.status_code == 200
        assert good.json()["segments"][0]["words"][0]["end"] == 0.55
        invalid = client.patch(
            f"/api/transcriptions/{job_id}/segments/{segment_id}/words/0",
            json={"start": 0.8, "end": 1.1, "text": "satu"},
        )
        assert invalid.status_code == 422
        blank_text = client.patch(
            f"/api/transcriptions/{job_id}/segments/{segment_id}/words/0",
            json={"start": 0.12, "end": 0.55, "text": "   "},
        )
        assert blank_text.status_code == 422



def test_transcribe_file_enables_and_normalizes_word_timestamps(monkeypatch, tmp_path):
    captured = {}

    class FakeWhisperModel:
        def __init__(self, model_name, device, compute_type):
            captured["model"] = (model_name, device, compute_type)

        def transcribe(self, source, **options):
            captured["source"] = source
            captured["options"] = options
            segment = SimpleNamespace(
                start=0.1,
                end=1.2,
                text="Halo dunia.",
                words=[
                    SimpleNamespace(start=0.1, end=0.45, word=" Halo", probability=0.98),
                    SimpleNamespace(start=0.5, end=0.9, word=" dunia.", probability=0.95),
                    SimpleNamespace(start=0.5, end=0.75, word="duplicate", probability=0.8),
                    SimpleNamespace(start=float("nan"), end=1.0, word="invalid", probability=0.8),
                    SimpleNamespace(start=1.0, end=1.1, word="bad-confidence", probability=1.5),
                ],
            )
            info = SimpleNamespace(duration=2.0, language="id", language_probability=0.99)
            return iter([segment]), info

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisperModel))
    progress_values = []
    source = tmp_path / "voice.mp4"
    source.write_bytes(b"video")

    result = transcription.transcribe_file(
        source, model_name="tiny", language="id", progress=progress_values.append
    )

    assert captured["options"]["word_timestamps"] is True
    assert captured["options"]["vad_filter"] is True
    assert result["segments"][0]["words"] == [
        {"start": 0.1, "end": 0.45, "text": "Halo", "probability": 0.98},
        {"start": 0.5, "end": 0.9, "text": "dunia.", "probability": 0.95},
    ]
    assert progress_values[-1] == 1.0


def test_ensure_storage_migrates_legacy_transcript_database(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    with TestClient(app) as client:
        media_id = seed_media()
        timestamp = main_module.now_iso()
        with database.get_connection() as connection:
            job_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO transcription_jobs(id,media_id,status,model_name,created_at,updated_at)
                   VALUES(?,?,'completed','tiny',?,?)""",
                (job_id, media_id, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO transcript_segments(job_id,start,end,text) VALUES(?,?,?,?)",
                (job_id, 0.1, 1.2, "Legacy words are not available"),
            )
            connection.execute("DROP TABLE transcript_words")
            connection.execute("DROP INDEX idx_transcription_queue")
            connection.execute("DROP INDEX idx_discovery_queue")
            connection.execute("ALTER TABLE transcription_jobs DROP COLUMN attempt_count")
            connection.execute("ALTER TABLE discovery_jobs DROP COLUMN attempt_count")

        database.ensure_storage()

        with database.get_connection() as connection:
            columns = connection.execute("PRAGMA table_info(transcript_words)").fetchall()
            assert {row["name"] for row in columns} >= {
                "job_id", "segment_id", "word_index", "start", "end", "text", "probability"
            }
            indexes = {
                row["name"]
                for row in connection.execute("PRAGMA index_list(transcript_words)").fetchall()
            }
            assert "idx_transcript_words_job_segment" in indexes
            transcription_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(transcription_jobs)").fetchall()
            }
            discovery_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(discovery_jobs)").fetchall()
            }
            assert "attempt_count" in transcription_columns
            assert "attempt_count" in discovery_columns
            queue_indexes = {
                row["name"]
                for row in connection.execute("PRAGMA index_list(transcription_jobs)").fetchall()
            }
            assert "idx_transcription_queue" in queue_indexes
        response = client.get(f"/api/transcriptions/{job_id}")
        assert response.status_code == 200
        assert response.json()["segments"][0]["text"] == "Legacy words are not available"
        assert response.json()["segments"][0]["words"] == []



def test_whisper_availability_handles_native_dll_load_error(monkeypatch):
    import builtins

    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    original_import = builtins.__import__

    def fail_native_whisper_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "faster_whisper":
            raise OSError("native CTranslate2 DLL is missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_native_whisper_import)

    assert transcription.is_available() is False



def test_delete_media_is_blocked_while_transcription_or_discovery_is_active(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    with TestClient(app) as client:
        media_id = seed_media()
        timestamp = main_module.now_iso()
        transcription_id = str(uuid.uuid4())
        discovery_id = str(uuid.uuid4())

        with database.get_connection() as connection:
            connection.execute(
                """INSERT INTO transcription_jobs(
                    id,media_id,status,model_name,created_at,updated_at
                ) VALUES(?,?,'queued','tiny',?,?)""",
                (transcription_id, media_id, timestamp, timestamp),
            )

        source_path = database.MEDIA_DIR / f"{media_id}.mp4"
        blocked_transcription = client.delete(f"/api/media/{media_id}")
        assert blocked_transcription.status_code == 409
        assert source_path.is_file()

        with database.get_connection() as connection:
            connection.execute(
                "UPDATE transcription_jobs SET status='failed' WHERE id=?",
                (transcription_id,),
            )
            connection.execute(
                """INSERT INTO discovery_jobs(
                    id,media_id,transcription_id,status,provider,model_name,mode,
                    min_duration,max_duration,created_at,updated_at
                ) VALUES(?,?,?,'processing','test','test-model','auto',15,60,?,?)""",
                (discovery_id, media_id, transcription_id, timestamp, timestamp),
            )

        blocked_discovery = client.delete(f"/api/media/{media_id}")
        assert blocked_discovery.status_code == 409
        assert source_path.is_file()

        with database.get_connection() as connection:
            connection.execute(
                "UPDATE discovery_jobs SET status='failed' WHERE id=?",
                (discovery_id,),
            )

        deleted = client.delete(f"/api/media/{media_id}")
        assert deleted.status_code == 204
        assert not source_path.exists()
        with database.get_connection() as connection:
            assert connection.execute(
                "SELECT 1 FROM media_assets WHERE id=?", (media_id,)
            ).fetchone() is None



def test_whisper_model_preflight_detects_cached_model_and_disk_space(tmp_path, monkeypatch):
    cache = tmp_path / "hf-cache"
    snapshot = cache / "models--Systran--faster-whisper-small" / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"cached")
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    monkeypatch.setattr(transcription.shutil, "disk_usage", lambda _path: SimpleNamespace(free=1))

    result = transcription.whisper_model_preflight("small")

    assert result["downloaded"] is True
    assert result["estimated_download_bytes"] == 0
    assert result["required_free_bytes"] == 0
    assert result["disk_space_ok"] is True


def test_whisper_model_preflight_estimates_missing_model_space(tmp_path, monkeypatch):
    cache = tmp_path / "hf-cache"
    cache.mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    monkeypatch.setattr(
        transcription.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=100 * 1024**2),
    )

    result = transcription.whisper_model_preflight("large-v3")

    assert result["downloaded"] is False
    assert result["estimated_download_bytes"] == 3090 * 1024**2
    assert result["required_free_bytes"] == 3602 * 1024**2
    assert result["disk_space_known"] is True
    assert result["disk_space_ok"] is False


def test_whisper_model_preflight_rejects_unknown_model():
    import pytest

    with pytest.raises(ValueError, match="tidak dikenal"):
        transcription.whisper_model_preflight("made-up-model")


def test_start_transcription_blocks_when_model_will_not_fit(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(transcription, "is_available", lambda: True)
    monkeypatch.setattr(
        transcription,
        "whisper_model_preflight",
        lambda model: {
            "model": model,
            "downloaded": False,
            "free_bytes": 100 * 1024**2,
            "required_free_bytes": 1000 * 1024**2,
            "disk_space_known": True,
            "disk_space_ok": False,
        },
    )
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/transcription",
            json={"model_name": "large-v3"},
        )
        assert response.status_code == 507
        assert "Ruang disk tidak cukup" in response.json()["detail"]
        with database.get_connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) AS count FROM transcription_jobs"
            ).fetchone()["count"] == 0
