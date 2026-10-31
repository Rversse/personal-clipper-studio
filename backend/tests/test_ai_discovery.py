from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app import ai_discovery, database, main as main_module
from app.main import app


def setup_test_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(database, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(main_module, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(main_module, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "test.sqlite3")


def seed_completed_transcript():
    project_id = str(uuid.uuid4())
    media_id = str(uuid.uuid4())
    transcript_id = str(uuid.uuid4())
    timestamp = "2026-10-31T00:00:00+00:00"
    with database.get_connection() as connection:
        connection.execute(
            "INSERT INTO projects(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
            (project_id, "Discovery test", "", timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO media_assets(
                id,project_id,original_name,stored_name,mime_type,file_size,created_at
            ) VALUES(?,?,?,?,?,?,?)""",
            (media_id, project_id, "sample.mp4", "sample.mp4", "video/mp4", 10, timestamp),
        )
        connection.execute(
            """INSERT INTO transcription_jobs(
                id,media_id,status,model_name,created_at,updated_at
            ) VALUES(?,?,'completed','tiny',?,?)""",
            (transcript_id, media_id, timestamp, timestamp),
        )
        connection.executemany(
            "INSERT INTO transcript_segments(job_id,start,end,text) VALUES(?,?,?,?)",
            [
                (transcript_id, float(start), float(start + 10), f"Segmen transkrip {start}.")
                for start in range(0, 60, 10)
            ],
        )
    return media_id, transcript_id


def test_discovery_job_persists_validated_candidates(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ai_discovery,
        "provider_settings",
        lambda: {"provider": "gemini", "model": "test-model"},
    )
    monkeypatch.setattr(
        ai_discovery,
        "analyze_transcript_chunk",
        lambda *args, **kwargs: [{
            "start_seconds": 1,
            "end_seconds": 56,
            "title": "Contoh klip",
            "hook": "Ini bagian pembuka yang menarik",
            "summary": "Ringkasan dari transkrip.",
            "rationale": "Memiliki konteks dan payoff yang utuh.",
            "score": 88,
            "category": "education",
        }],
    )

    with TestClient(app) as client:
        media_id, transcript_id = seed_completed_transcript()
        response = client.post(
            f"/api/media/{media_id}/discovery",
            json={
                "transcription_id": transcript_id,
                "mode": "auto",
                "min_duration": 30,
                "max_duration": 60,
            },
        )
        assert response.status_code == 202
        result = client.get(f"/api/discovery/{response.json()['id']}")
        assert result.status_code == 200
        payload = result.json()
        assert payload["status"] == "completed"
        assert payload["progress"] == 1
        assert len(payload["candidates"]) == 1
        candidate = payload["candidates"][0]
        assert candidate["start_seconds"] == 0
        assert candidate["end_seconds"] == 60
        assert candidate["score"] == 88


def test_discovery_requires_completed_transcript(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ai_discovery,
        "provider_settings",
        lambda: {"provider": "gemini", "model": "test-model"},
    )
    with TestClient(app) as client:
        media_id, transcript_id = seed_completed_transcript()
        with database.get_connection() as connection:
            connection.execute(
                "UPDATE transcription_jobs SET status='processing' WHERE id=?",
                (transcript_id,),
            )
        response = client.post(
            f"/api/media/{media_id}/discovery",
            json={"transcription_id": transcript_id},
        )
        assert response.status_code == 409


def test_transcript_chunking_keeps_overlap_and_progresses():
    segments = [
        {"start": float(index * 20), "end": float(index * 20 + 10), "text": f"segment {index}"}
        for index in range(40)
    ]
    chunks = ai_discovery.split_transcript(segments, max_seconds=120, overlap_seconds=30)
    assert len(chunks) > 1
    assert all(chunk for chunk in chunks)
    assert chunks[0][-1]["end"] > chunks[1][0]["start"]
    assert chunks[-1][-1]["end"] == segments[-1]["end"]


def test_discovery_fails_fast_without_gemini_key(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setenv("PCS_AI_PROVIDER", "gemini")
    monkeypatch.delenv("PCS_GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        media_id, transcript_id = seed_completed_transcript()
        response = client.post(
            f"/api/media/{media_id}/discovery",
            json={"transcription_id": transcript_id},
        )
        assert response.status_code == 503
        assert "API key belum dikonfigurasi" in response.json()["detail"]


def test_candidate_timestamp_validation_rejects_out_of_range():
    from app.main import normalize_candidate

    transcript = [
        {"start": 0.0, "end": 10.0, "text": "Bagian awal."},
        {"start": 10.0, "end": 20.0, "text": "Bagian akhir."},
    ]
    invalid = {
        "start_seconds": 100,
        "end_seconds": 150,
        "title": "Invalid",
        "hook": "Invalid",
        "summary": "Invalid",
        "rationale": "Invalid",
        "score": 90,
        "category": "other",
    }
    assert normalize_candidate(invalid, transcript, 15, 60) is None


def test_provider_parser_handles_json_code_fences_and_thinking():
    parsed = ai_discovery._extract_json(
        '<think>reasoning is not part of the response</think>\n```json\n{"candidates": []}\n```'
    )
    assert parsed == {"candidates": []}
