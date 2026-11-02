from __future__ import annotations

import uuid

import pytest

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


def wait_for_discovery(client, job_id: str, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/discovery/{job_id}")
        assert response.status_code == 200
        latest = response.json()
        if latest["status"] in {"completed", "failed", "cancelled"}:
            return latest
        time.sleep(0.02)
    raise AssertionError(f"Discovery job did not finish; last state: {latest}")


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



def test_queued_discovery_can_be_cancelled(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ai_discovery, "provider_settings",
        lambda: {"provider": "gemini", "model": "test-model"},
    )
    with TestClient(app) as client:
        media_id, transcript_id = seed_completed_transcript()
        # Insert a queued row directly to exercise cancellation deterministically.
        job_id = str(uuid.uuid4())
        timestamp = main_module.now_iso()
        with database.get_connection() as connection:
            connection.execute(
                """INSERT INTO discovery_jobs(
                    id,media_id,transcription_id,status,provider,model_name,mode,
                    min_duration,max_duration,created_at,updated_at
                ) VALUES(?,?,?,'queued','gemini','test-model','auto',30,60,?,?)""",
                (job_id, media_id, transcript_id, timestamp, timestamp),
            )
        response = client.post(f"/api/discovery/{job_id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert "dibatalkan" in response.json()["error"]



def test_active_discovery_discards_provider_results_after_cancel(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    with TestClient(app):
        media_id, transcript_id = seed_completed_transcript()
        job_id = str(uuid.uuid4())
        timestamp = main_module.now_iso()
        with database.get_connection() as connection:
            connection.execute(
                """INSERT INTO discovery_jobs(
                    id,media_id,transcription_id,status,provider,model_name,mode,
                    min_duration,max_duration,created_at,updated_at
                ) VALUES(?,?,?,'queued','gemini','test-model','auto',30,60,?,?)""",
                (job_id, media_id, transcript_id, timestamp, timestamp),
            )

        def fake_analyze(*args, **kwargs):
            main_module._JOB_CANCEL_EVENTS[job_id].set()
            return [{
                "start_seconds": 0, "end_seconds": 60, "title": "Should be discarded",
                "hook": "Hook", "summary": "Summary", "rationale": "Rationale",
                "score": 90, "category": "education",
            }]

        monkeypatch.setattr(ai_discovery, "analyze_transcript_chunk", fake_analyze)
        main_module.run_discovery_job(job_id)

        with database.get_connection() as connection:
            job = connection.execute(
                "SELECT status,error FROM discovery_jobs WHERE id=?", (job_id,)
            ).fetchone()
            candidates = connection.execute(
                "SELECT COUNT(*) AS count FROM clip_candidates WHERE discovery_job_id=?",
                (job_id,),
            ).fetchone()["count"]
        assert job["status"] == "cancelled"
        assert "dibatalkan" in job["error"]
        assert candidates == 0


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
        payload = wait_for_discovery(client, response.json()["id"])
        assert payload["status"] == "completed"
        assert payload["progress"] == 1
        assert len(payload["candidates"]) == 1
        candidate = payload["candidates"][0]
        assert candidate["start_seconds"] == 0
        assert candidate["end_seconds"] == 60
        assert candidate["score"] == 88
        assert candidate["boundary_quality"] >= 50
        assert candidate["opening_boundary"]
        assert candidate["ending_boundary"]
        assert candidate["context_note"]
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


def test_gemini_provider_settings_reads_key_from_secure_store(monkeypatch):
    from app import credentials

    monkeypatch.setenv("PCS_AI_PROVIDER", "gemini")
    monkeypatch.delenv("PCS_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("PCS_GEMINI_MODEL", "gemini-test-model")
    monkeypatch.setattr(credentials, "_windows_backend", lambda: type(
        "Backend",
        (),
        {"get_password": lambda self, service, account: "vault-secret"},
    )())

    settings = ai_discovery.provider_settings()

    assert settings == {
        "provider": "gemini",
        "api_key": "vault-secret",
        "model": "gemini-test-model",
    }


def test_gemini_provider_settings_handles_credential_store_failure(monkeypatch):
    from app import credentials

    monkeypatch.setenv("PCS_AI_PROVIDER", "gemini")
    monkeypatch.delenv("PCS_GEMINI_API_KEY", raising=False)

    def fail_read():
        raise credentials.CredentialStoreError("internal vault detail")

    monkeypatch.setattr(credentials, "get_gemini_api_key", fail_read)
    with pytest.raises(ai_discovery.DiscoveryProviderError) as error:
        ai_discovery.provider_settings()

    assert "internal vault detail" not in str(error.value)
    assert "penyimpanan kredensial Windows" in str(error.value)


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

def test_boundary_refinement_uses_full_transcript_context_at_chunk_edges():
    from app.main import normalize_candidate

    context = [
        {"start": 0.0, "end": 10.0, "text": "Kalimat konteks yang selesai."},
        {"start": 10.0, "end": 20.0, "text": "Dan inilah pembahasan yang menjadi inti."},
        {"start": 20.0, "end": 30.0, "text": "Kesimpulan akhirnya cukup jelas."},
        {"start": 30.0, "end": 40.0, "text": "Ada satu tambahan."},
    ]
    chunk = context[1:]
    raw = {
        "start_seconds": 11, "end_seconds": 29,
        "title": "Kandidat chunk", "hook": "Awal yang jelas",
        "summary": "Ringkasan.", "rationale": "Konteks utuh.",
        "score": 80, "category": "education",
    }
    candidate = normalize_candidate(raw, chunk, 15, 30, context_segments=context)
    assert candidate is not None
    assert candidate["start_seconds"] == 10
    assert "awal transkrip" not in candidate["opening_boundary"]



def test_word_level_boundary_refinement_snaps_to_complete_words_and_pauses():
    from app.clip_boundaries import refine_candidate_boundaries

    transcript = [{
        "start": 0.0,
        "end": 5.0,
        "text": "Kita perlu memahami masalah. Solusinya ternyata sangat sederhana.",
        "words": [
            {"start": 0.0, "end": 0.3, "text": "Kita", "probability": 0.99},
            {"start": 0.3, "end": 0.7, "text": "perlu", "probability": 0.98},
            {"start": 0.7, "end": 1.2, "text": "memahami", "probability": 0.97},
            {"start": 1.2, "end": 2.1, "text": "masalah.", "probability": 0.99},
            {"start": 2.6, "end": 3.1, "text": "Solusinya", "probability": 0.98},
            {"start": 3.1, "end": 3.5, "text": "ternyata", "probability": 0.97},
            {"start": 3.5, "end": 3.9, "text": "sangat", "probability": 0.98},
            {"start": 3.9, "end": 4.8, "text": "sederhana.", "probability": 0.99},
        ],
    }]

    refined = refine_candidate_boundaries(2.7, 4.1, transcript, 2, 3)

    assert refined is not None
    assert refined["start_seconds"] == 2.6
    assert refined["end_seconds"] == 4.8
    assert "Presisi kata" in refined["opening_boundary"]
    assert "Presisi kata" in refined["ending_boundary"]
    assert "jeda alami" in refined["opening_boundary"]
    assert "akhir kalimat" in refined["ending_boundary"]


def test_invalid_word_timestamps_fall_back_to_segment_boundaries():
    from app.clip_boundaries import refine_candidate_boundaries

    transcript = [
        {
            "start": 0.0, "end": 10.0, "text": "Pembuka lengkap.",
            "words": [
                {"start": -1.0, "end": 0.2, "text": "invalid"},
                {"start": 8.0, "end": 7.0, "text": "reversed"},
            ],
        },
        {
            "start": 10.0, "end": 20.0, "text": "Jawaban akhirnya selesai.",
            "words": [{"start": 19.0, "end": 25.0, "text": "invalid"}],
        },
    ]

    refined = refine_candidate_boundaries(1.0, 19.0, transcript, 15, 25)

    assert refined is not None
    assert refined["start_seconds"] == 0.0
    assert refined["end_seconds"] == 20.0
    assert "Presisi kata" not in refined["opening_boundary"]


def test_word_level_boundary_refinement_preserves_duration_constraints():
    from app.clip_boundaries import refine_candidate_boundaries

    transcript = [{
        "start": 0.0,
        "end": 20.0,
        "text": "Kalimat pembuka. Kalimat penutup.",
        "words": [
            {"start": 0.0, "end": 1.0, "text": "Kalimat"},
            {"start": 1.0, "end": 2.0, "text": "pembuka."},
            {"start": 18.0, "end": 19.0, "text": "Kalimat"},
            {"start": 19.0, "end": 20.0, "text": "penutup."},
        ],
    }]

    refined = refine_candidate_boundaries(1.0, 19.0, transcript, 5, 10)

    assert refined is None


def test_discovery_job_loads_persisted_words_for_candidate_refinement(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ai_discovery,
        "provider_settings",
        lambda: {"provider": "gemini", "model": "test-model"},
    )

    def fake_analyze(segments, **kwargs):
        assert any(segment.get("words") for segment in segments)
        return [{
            "start_seconds": 11,
            "end_seconds": 56,
            "title": "Jawaban utuh",
            "hook": "Jawabannya ternyata sederhana",
            "summary": "Konteks dan jawaban lengkap.",
            "rationale": "Pembuka serta penutup tidak memotong kata.",
            "score": 90,
            "category": "education",
        }]

    monkeypatch.setattr(ai_discovery, "analyze_transcript_chunk", fake_analyze)
    monkeypatch.setattr(
        main_module.audio_analysis,
        "analyze_audio",
        lambda source, duration=None: {
            "energy_samples": [],
            "silences": [
                {"start": 9.7, "end": 10.1, "duration": 0.4},
                {"start": 59.9, "end": 60.4, "duration": 0.5},
            ],
        },
    )

    with TestClient(app) as client:
        media_id, transcript_id = seed_completed_transcript()
        with database.get_connection() as connection:
            segments = connection.execute(
                "SELECT id,start,end FROM transcript_segments WHERE job_id=? ORDER BY start",
                (transcript_id,),
            ).fetchall()
            connection.executemany(
                """INSERT INTO transcript_words(
                    job_id,segment_id,word_index,start,end,text,probability
                ) VALUES(?,?,?,?,?,?,?)""",
                [
                    (
                        transcript_id, row["id"], 0,
                        float(row["start"]) + 0.1,
                        float(row["end"]) - 0.1,
                        f"kata-{int(row['start'])}.",
                        0.95,
                    )
                    for row in segments
                ],
            )

        created = client.post(
            f"/api/media/{media_id}/discovery",
            json={
                "transcription_id": transcript_id,
                "mode": "auto",
                "min_duration": 30,
                "max_duration": 60,
            },
        )
        assert created.status_code == 202
        result = wait_for_discovery(client, created.json()["id"])
        candidate = result["candidates"][0]
        assert candidate["start_seconds"] == 10.1
        assert candidate["end_seconds"] == 59.9
        assert "Presisi kata" in candidate["opening_boundary"]
        assert "Presisi kata" in candidate["ending_boundary"]



def test_word_continuation_detection_handles_standalone_tokens():
    from app.clip_boundaries import _word_starts_continuation

    assert _word_starts_continuation("Dan")
    assert _word_starts_continuation("karena")
    assert _word_starts_continuation("yang,")
    assert not _word_starts_continuation("dunia.")



def test_audio_pause_prosody_adjusts_segment_boundaries():
    from app.clip_boundaries import refine_candidate_boundaries

    transcript = [
        {"start": 0.0, "end": 10.0, "text": "Konteks sebelumnya sudah selesai."},
        {"start": 10.0, "end": 20.0, "text": "Jawaban akhirnya sudah lengkap."},
    ]
    audio_features = {
        "energy_samples": [],
        "silences": [
            {"start": 9.7, "end": 10.0, "duration": 0.3},
            {"start": 20.0, "end": 20.5, "duration": 0.5},
        ],
    }

    refined = refine_candidate_boundaries(
        10.1, 19.0, transcript, 5, 15, audio_features=audio_features
    )

    assert refined is not None
    assert refined["start_seconds"] == 10.0
    assert refined["end_seconds"] == 20.0
    assert "jeda audio alami" in refined["opening_boundary"]
    assert "jeda audio alami" in refined["ending_boundary"]



def test_rms_energy_changes_adjust_opening_and_ending_signals():
    from app.clip_boundaries import refine_candidate_boundaries

    transcript = [
        {"start": 0.0, "end": 10.0, "text": "Konteks sebelumnya sudah selesai."},
        {"start": 10.0, "end": 20.0, "text": "Jawaban akhirnya sudah lengkap."},
    ]
    audio_features = {
        "energy_samples": [
            {"time": 8.8, "rms_db": -34.0},
            {"time": 9.2, "rms_db": -32.0},
            {"time": 10.3, "rms_db": -16.0},
            {"time": 10.8, "rms_db": -15.0},
            {"time": 18.6, "rms_db": -15.0},
            {"time": 19.2, "rms_db": -16.0},
            {"time": 20.3, "rms_db": -34.0},
            {"time": 20.8, "rms_db": -32.0},
        ],
        "silences": [],
    }

    refined = refine_candidate_boundaries(
        10.1, 19.0, transcript, 5, 15, audio_features=audio_features
    )

    assert refined is not None
    assert "energi bicara menguat" in refined["opening_boundary"]
    assert "energi bicara mereda" in refined["ending_boundary"]



def test_audio_feature_index_matches_raw_scoring_and_sorts_out_of_order_samples():
    from app.clip_boundaries import _audio_boundary_adjustment, _prepare_audio_features

    features = {
        "energy_samples": [
            {"time": 10.8, "rms_db": -15.0},
            {"time": 8.8, "rms_db": -34.0},
            {"time": 10.3, "rms_db": -16.0},
            {"time": 9.2, "rms_db": -32.0},
        ],
        "silences": [
            {"start": 19.9, "end": 20.4, "duration": 0.5},
            {"start": 9.7, "end": 10.0, "duration": 0.3},
            {"start": float("nan"), "end": 30.0, "duration": 1.0},
        ],
    }

    prepared = _prepare_audio_features(features)

    assert prepared is not None
    assert prepared is _prepare_audio_features(prepared)
    assert _audio_boundary_adjustment(10.0, prepared, "opening") == _audio_boundary_adjustment(
        10.0, features, "opening"
    )
    assert _audio_boundary_adjustment(20.0, prepared, "ending") == _audio_boundary_adjustment(
        20.0, features, "ending"
    )


def test_candidate_refinement_prepares_audio_features_once(monkeypatch):
    from app import clip_boundaries

    original_prepare = clip_boundaries._prepare_audio_features
    calls = []

    def tracked_prepare(features):
        calls.append(features)
        return original_prepare(features)

    monkeypatch.setattr(clip_boundaries, "_prepare_audio_features", tracked_prepare)
    transcript = [
        {"start": 0.0, "end": 10.0, "text": "Konteks sebelumnya sudah selesai."},
        {"start": 10.0, "end": 20.0, "text": "Jawaban akhirnya sudah lengkap."},
    ]
    features = {
        "energy_samples": [
            {"time": 9.0, "rms_db": -35.0},
            {"time": 10.5, "rms_db": -15.0},
            {"time": 19.0, "rms_db": -15.0},
            {"time": 20.5, "rms_db": -35.0},
        ],
        "silences": [],
    }

    refined = clip_boundaries.refine_candidate_boundaries(
        10.1, 19.0, transcript, 5, 15, audio_features=features
    )

    assert refined is not None
    assert len(calls) == 1
