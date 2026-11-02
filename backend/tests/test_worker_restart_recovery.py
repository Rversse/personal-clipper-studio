"""Restart-recovery integration coverage for durable background workers."""
from __future__ import annotations

import json
import uuid

from app import ai_discovery, audio_analysis, database, main as main_module, rendering, transcription


def setup_storage(tmp_path, monkeypatch):
    media_dir = tmp_path / "media"
    thumbnail_dir = tmp_path / "thumbnails"
    export_dir = tmp_path / "exports"
    monkeypatch.setattr(database, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(database, "THUMBNAIL_DIR", thumbnail_dir)
    monkeypatch.setattr(database, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "studio.sqlite3")
    monkeypatch.setattr(main_module, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(main_module, "THUMBNAIL_DIR", thumbnail_dir)
    monkeypatch.setattr(main_module, "EXPORT_DIR", export_dir)
    database.ensure_storage()
    media_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)


def seed_media_and_jobs():
    project_id = str(uuid.uuid4())
    media_id = str(uuid.uuid4())
    transcript_id = str(uuid.uuid4())
    discovery_id = str(uuid.uuid4())
    export_id = str(uuid.uuid4())
    timestamp = main_module.now_iso()
    source = database.MEDIA_DIR / f"{media_id}.mp4"
    source.write_bytes(b"synthetic-source")
    with database.get_connection() as connection:
        connection.execute(
            "INSERT INTO projects(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
            (project_id, "Restart recovery", "", timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO media_assets(
                id,project_id,original_name,stored_name,mime_type,file_size,duration,created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (media_id, project_id, "source.mp4", source.name, "video/mp4", source.stat().st_size, 60.0, timestamp),
        )
        connection.execute(
            """INSERT INTO transcription_jobs(
                id,media_id,status,model_name,language,progress,attempt_count,created_at,updated_at
            ) VALUES(?,?,'processing','tiny','id',0.6,1,?,?)""",
            (transcript_id, media_id, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO discovery_jobs(
                id,media_id,transcription_id,status,provider,model_name,mode,category,
                min_duration,max_duration,progress,attempt_count,created_at,updated_at
            ) VALUES(?,?,?,'processing','gemini','test-model','auto','education',15,60,0.5,1,?,?)""",
            (discovery_id, media_id, transcript_id, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO export_jobs(
                id,media_id,status,start_seconds,end_seconds,aspect_ratio,output_name,
                clip_segments,render_settings,progress,created_at,updated_at
            ) VALUES(?,?,'processing',0,40,'9:16',?,?,?,0.7,?,?)""",
            (
                export_id, media_id, f"clip-{export_id}.mp4",
                json.dumps([{"start": 0, "end": 40}, {"start": 40, "end": 60}]),
                json.dumps({"quality_profile": "balanced", "frame_rate": 30, "normalize_audio": False}),
                timestamp, timestamp,
            ),
        )
    return media_id, transcript_id, discovery_id, export_id, source


def test_transcription_discovery_and_export_resume_after_restart(tmp_path, monkeypatch):
    setup_storage(tmp_path, monkeypatch)
    media_id, transcript_id, discovery_id, export_id, source = seed_media_and_jobs()
    main_module._JOB_SHUTDOWN.clear()
    main_module._EXPORT_WORKER_STOP.clear()

    # Simulate a new application process opening the same persistent database.
    main_module._recover_interrupted_jobs()
    main_module.recover_interrupted_exports()

    with database.get_connection() as connection:
        states = {
            "transcription": connection.execute(
                "SELECT status,progress,attempt_count FROM transcription_jobs WHERE id=?",
                (transcript_id,),
            ).fetchone(),
            "discovery": connection.execute(
                "SELECT status,progress,attempt_count FROM discovery_jobs WHERE id=?",
                (discovery_id,),
            ).fetchone(),
            "export": connection.execute(
                "SELECT status,progress FROM export_jobs WHERE id=?", (export_id,)
            ).fetchone(),
        }
    assert tuple(states["transcription"]) == ("queued", 0.0, 1)
    assert tuple(states["discovery"]) == ("queued", 0.0, 1)
    assert tuple(states["export"]) == ("queued", 0.0)

    def fake_transcribe(path, model_name, language, progress, cancel_check=None):
        assert path == source
        assert model_name == "tiny"
        assert language == "id"
        progress(0.4)
        if cancel_check:
            cancel_check()
        return {
            "language": "id",
            "language_probability": 0.99,
            "duration": 60.0,
            "segments": [
                {
                    "start": float(start),
                    "end": float(start + 10),
                    "text": f"Kalimat nomor {index}.",
                    "words": [],
                }
                for index, start in enumerate(range(0, 60, 10))
            ],
        }

    monkeypatch.setattr(transcription, "transcribe_file", fake_transcribe)
    main_module.run_transcription_job(transcript_id, str(source), "tiny", "id")

    monkeypatch.setattr(ai_discovery, "provider_settings", lambda: {
        "provider": "gemini", "model": "test-model"
    })
    monkeypatch.setattr(audio_analysis, "analyze_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai_discovery, "analyze_transcript_chunk", lambda *args, **kwargs: [{
        "start_seconds": 0,
        "end_seconds": 40,
        "title": "Recovered candidate",
        "hook": "Hook yang valid",
        "summary": "Ringkasan kandidat hasil pemulihan.",
        "rationale": "Konteks dan payoff tetap utuh.",
        "score": 90,
        "category": "education",
    }])
    monkeypatch.setattr(main_module, "refine_candidate_boundaries", lambda *args, **kwargs: {
        "start_seconds": 0.0,
        "end_seconds": 40.0,
        "boundary_quality": 90,
        "opening_boundary": "Awal kalimat",
        "ending_boundary": "Akhir kalimat",
        "context_note": "Konteks lengkap",
    })
    main_module.run_discovery_job(discovery_id)

    def fake_render(source_path, destination, clip_segments, aspect_ratio, **kwargs):
        assert source_path == source
        assert len(clip_segments) == 2
        assert aspect_ratio == "9:16"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"recovered-mp4-artifact")
        return {"encoder": "libx264"}

    monkeypatch.setattr(rendering, "render_sequence", fake_render)
    output = database.EXPORT_DIR / f"clip-{export_id}.mp4"
    main_module.run_export_job(export_id, str(source), str(output))

    with database.get_connection() as connection:
        transcription_row = connection.execute(
            "SELECT status,attempt_count FROM transcription_jobs WHERE id=?", (transcript_id,)
        ).fetchone()
        discovery = connection.execute(
            "SELECT status,attempt_count FROM discovery_jobs WHERE id=?", (discovery_id,)
        ).fetchone()
        candidate_count = connection.execute(
            "SELECT COUNT(*) FROM clip_candidates WHERE discovery_job_id=?", (discovery_id,)
        ).fetchone()[0]
        export = connection.execute(
            "SELECT status,progress,encoder FROM export_jobs WHERE id=?", (export_id,)
        ).fetchone()
        segment_count = connection.execute(
            "SELECT COUNT(*) FROM transcript_segments WHERE job_id=?", (transcript_id,)
        ).fetchone()[0]

    assert tuple(transcription_row) == ("completed", 2)
    assert tuple(discovery) == ("completed", 2)
    assert candidate_count == 1
    assert segment_count == 6
    assert tuple(export) == ("completed", 1.0, "libx264")
    assert output.is_file()
    assert output.read_bytes() == b"recovered-mp4-artifact"



def test_shutdown_requeues_active_transcription_and_discovery_without_burning_attempts(tmp_path, monkeypatch):
    setup_storage(tmp_path, monkeypatch)
    _, transcript_id, discovery_id, _, _ = seed_media_and_jobs()
    main_module._JOB_SHUTDOWN.clear()
    main_module._JOB_WORKER_STOP.clear()
    monkeypatch.setattr(main_module, "_TRANSCRIPTION_WORKER_THREAD", None)
    monkeypatch.setattr(main_module, "_DISCOVERY_WORKER_THREAD", None)

    main_module.stop_job_workers()

    with database.get_connection() as connection:
        transcription_state = connection.execute(
            "SELECT status,progress,attempt_count,error FROM transcription_jobs WHERE id=?",
            (transcript_id,),
        ).fetchone()
        discovery_state = connection.execute(
            "SELECT status,progress,attempt_count,error FROM discovery_jobs WHERE id=?",
            (discovery_id,),
        ).fetchone()

    assert tuple(transcription_state) == ("queued", 0.0, 0, None)
    assert tuple(discovery_state) == ("queued", 0.0, 0, None)
    assert main_module._JOB_SHUTDOWN.is_set()
    assert main_module._JOB_WORKER_STOP.is_set()


def test_job_control_registered_after_shutdown_is_cancelled_immediately(tmp_path, monkeypatch):
    setup_storage(tmp_path, monkeypatch)
    main_module._JOB_SHUTDOWN.set()
    event = main_module._register_job_control("late-worker-registration")
    try:
        assert event.is_set()
    finally:
        main_module._unregister_job_control("late-worker-registration")
        main_module._JOB_SHUTDOWN.clear()


def test_export_worker_shutdown_race_requeues_before_render_starts(tmp_path, monkeypatch):
    setup_storage(tmp_path, monkeypatch)
    _, _, _, export_id, source = seed_media_and_jobs()
    main_module._EXPORT_WORKER_STOP.clear()
    main_module.recover_interrupted_exports()
    main_module._EXPORT_WORKER_STOP.set()
    monkeypatch.setattr(main_module, "_is_completed_export", lambda _path: False)

    def render_must_not_start(*args, **kwargs):
        raise AssertionError("FFmpeg must not start after export worker shutdown.")

    monkeypatch.setattr(rendering, "render_sequence", render_must_not_start)
    output = database.EXPORT_DIR / f"clip-{export_id}.mp4"
    main_module.run_export_job(export_id, str(source), str(output))

    with database.get_connection() as connection:
        state = connection.execute(
            "SELECT status,progress,encoder,error FROM export_jobs WHERE id=?",
            (export_id,),
        ).fetchone()
    assert tuple(state) == ("queued", 0.0, None, None)
    assert not output.exists()
    assert rendering.get_control(export_id) is None
    main_module._EXPORT_WORKER_STOP.clear()
