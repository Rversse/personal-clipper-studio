from __future__ import annotations

import uuid

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

    def fake_transcribe(path, model_name, language, progress):
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

        result = client.get(f"/api/transcriptions/{job_id}")
        assert result.status_code == 200
        payload = result.json()
        assert payload["status"] == "completed"
        assert payload["detected_language"] == "id"
        assert payload["progress"] == 1
        assert payload["segments"] == [
            {"id": payload["segments"][0]["id"], "start": 0.2, "end": 2.4, "text": "Halo semuanya.", "words": []}
        ]



def test_word_timestamps_are_persisted_and_returned(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(transcription, "is_available", lambda: True)

    def fake_transcribe(path, model_name, language, progress):
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
        payload = client.get(f"/api/transcriptions/{job_id}").json()
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

    def fake_transcribe(path, model_name, language, progress):
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
        segment = client.get(f"/api/transcriptions/{job_id}").json()["segments"][0]
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
