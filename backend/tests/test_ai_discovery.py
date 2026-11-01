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
        latest_discovery = client.get(f"/api/media/{media_id}/discovery/latest")
        assert latest_discovery.status_code == 200
        assert latest_discovery.json()["id"] == payload["id"]
        latest_transcription = client.get(f"/api/media/{media_id}/transcriptions/latest")
        assert latest_transcription.status_code == 200
        assert latest_transcription.json()["id"] == transcript_id


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
    text = (
        "<think>reasoning is not part of the response</think>"
        + chr(10) + "```json" + chr(10)
        + '{"candidates": []}' + chr(10) + "```"
    )
    parsed = ai_discovery._extract_json(text)
    assert parsed == {"candidates": []}


def test_context_aware_boundaries_prefer_complete_sentence_and_payoff():
    from app.clip_boundaries import refine_candidate_boundaries

    transcript = [
        {"start": 0.0, "end": 10.0, "text": "Ini konteks awal yang penting."},
        {"start": 10.0, "end": 17.0, "text": "Karena kalau dilakukan begitu saja"},
        {"start": 17.0, "end": 24.0, "text": "hasilnya justru menjadi lebih buruk."},
        {"start": 24.0, "end": 32.0, "text": "Dan inilah bagian yang mengejutkan."},
        {"start": 32.0, "end": 40.0, "text": "Semua akhirnya bisa diselesaikan."},
    ]
    refined = refine_candidate_boundaries(11.0, 18.0, transcript, 12, 35)
    assert refined is not None
    assert refined["start_seconds"] == 10.0
    assert refined["end_seconds"] == 24.0
    assert refined["boundary_quality"] >= 60
    assert "kata sambung" in refined["opening_boundary"]
    assert "akhir kalimat" in refined["ending_boundary"]


def test_context_aware_boundary_refinement_includes_lead_in_when_semantic_anchor_is_late():
    from app.clip_boundaries import refine_candidate_boundaries

    transcript = [
        {"start": 0.0, "end": 8.0, "text": "Ada satu hal yang harus dipahami terlebih dahulu."},
        {"start": 8.0, "end": 15.0, "text": "Sebab tanpa konteks itu"},
        {"start": 15.0, "end": 23.0, "text": "orang akan salah memahami hasil akhirnya."},
        {"start": 23.0, "end": 30.0, "text": "Dan ini menjadi pelajaran penting."},
        {"start": 30.0, "end": 38.0, "text": "Itulah alasan mengapa prosesnya harus utuh."},
    ]
    refined = refine_candidate_boundaries(17.0, 30.0, transcript, 15, 35)
    assert refined is not None
    assert refined["start_seconds"] == 8.0
    assert refined["end_seconds"] == 30.0
    assert refined["boundary_quality"] >= 50


def test_context_aware_boundary_refinement_rejects_duration_outside_window():
    from app.clip_boundaries import refine_candidate_boundaries

    transcript = [
        {"start": 0.0, "end": 5.0, "text": "Kalimat pertama."},
        {"start": 5.0, "end": 10.0, "text": "Kalimat kedua."},
        {"start": 10.0, "end": 15.0, "text": "Kalimat ketiga."},
    ]
    assert refine_candidate_boundaries(0.0, 5.0, transcript, 15, 30) is None


def test_normalize_candidate_returns_boundary_quality_metadata():
    from app.main import normalize_candidate

    transcript = [
        {"start": 0.0, "end": 10.0, "text": "Pembuka yang lengkap."},
        {"start": 10.0, "end": 20.0, "text": "Penjelasan berikutnya."},
        {"start": 20.0, "end": 30.0, "text": "Kesimpulan yang jelas."},
        {"start": 30.0, "end": 40.0, "text": "Tambahan konteks."},
        {"start": 40.0, "end": 50.0, "text": "Jawaban akhirnya ditemukan."},
        {"start": 50.0, "end": 60.0, "text": "Cerita selesai."},
    ]
    raw = {
        "start_seconds": 1, "end_seconds": 56,
        "title": "Kandidat", "hook": "Pembuka yang kuat",
        "summary": "Ringkasan.", "rationale": "Utuh.",
        "score": 88, "category": "education",
    }
    candidate = normalize_candidate(raw, transcript, 30, 60)
    assert candidate is not None
    assert candidate["start_seconds"] == 0
    assert candidate["end_seconds"] == 60
    assert 0 <= candidate["boundary_quality"] <= 100
    assert candidate["opening_boundary"]
    assert candidate["ending_boundary"]
    assert candidate["context_note"]
