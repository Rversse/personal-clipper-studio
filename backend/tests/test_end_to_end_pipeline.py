"""End-to-end API/persistence regression for the media-to-export workflow.

External ML providers and FFmpeg are replaced with deterministic fakes here. The
existing rendering tests separately exercise real FFmpeg output when available.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import ai_discovery, audio_analysis, database, main as main_module, rendering, transcription
from app.main import app


def setup_test_storage(tmp_path, monkeypatch):
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
    # Drive each durable job synchronously below so the test controls every
    # transition and cannot race background worker threads.
    monkeypatch.setattr(main_module, "start_job_workers", lambda: None)
    monkeypatch.setattr(main_module, "start_export_worker", lambda: None)
    monkeypatch.setattr(main_module, "stop_job_workers", lambda: None)


def test_import_transcribe_discover_and_export_pipeline(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    observed = {}

    def fake_probe(path):
        assert path.is_file()
        return {
            "duration": 120.0,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "video_codec": "h264",
            "audio_codec": "aac",
        }

    def fake_thumbnail(source, destination):
        assert source.is_file()
        destination.write_bytes(b"synthetic-thumbnail")
        return True

    def fake_transcribe(path, model_name, language, progress, cancel_check=None):
        assert path.is_file()
        assert model_name == "tiny"
        assert language == "id"
        segments = []
        for index in range(6):
            start = float(index * 10)
            text = f"Halo dunia nomor {index}."
            words = [
                {"start": start + 0.2, "end": start + 1.0, "text": "Halo", "probability": 0.99},
                {"start": start + 1.1, "end": start + 2.0, "text": "dunia", "probability": 0.98},
                {"start": start + 2.1, "end": start + 3.0, "text": "nomor", "probability": 0.97},
                {"start": start + 3.1, "end": start + 3.5, "text": str(index), "probability": 0.96},
                {"start": start + 3.6, "end": start + 3.8, "text": ".", "probability": 0.95},
            ]
            segments.append({
                "start": start,
                "end": start + 10.0,
                "text": text,
                "words": words,
            })
        progress(0.75)
        if cancel_check:
            cancel_check()
        return {
            "language": "id",
            "language_probability": 0.99,
            "duration": 120.0,
            "segments": segments,
        }

    def fake_discovery(*args, **kwargs):
        return [{
            "start_seconds": 1,
            "end_seconds": 56,
            "title": "Momen menarik",
            "hook": "Pembukaan yang langsung menarik perhatian",
            "summary": "Ringkasan momen dari video.",
            "rationale": "Memiliki konteks dan payoff yang utuh.",
            "score": 91,
            "category": "education",
        }]

    def fake_render(source, destination, clip_segments, aspect_ratio, **kwargs):
        assert source.is_file()
        observed["clip_segments"] = clip_segments
        observed["aspect_ratio"] = aspect_ratio
        observed["subtitles"] = kwargs["subtitle_segments"]
        observed["quality_profile"] = kwargs["quality_profile"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A deterministic MP4-shaped artifact; real encoding is covered by
        # test_real_ffmpeg_sequence_render_produces_vertical_h264_aac_mp4.
        destination.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"x" * 2048)
        return {"encoder": "libx264"}

    monkeypatch.setattr(main_module, "probe_media", fake_probe)
    monkeypatch.setattr(main_module, "create_thumbnail", fake_thumbnail)
    monkeypatch.setattr(transcription, "is_available", lambda: True)
    monkeypatch.setattr(
        transcription,
        "whisper_model_preflight",
        lambda _model: {"disk_space_known": False, "disk_space_ok": True},
    )
    monkeypatch.setattr(transcription, "transcribe_file", fake_transcribe)
    monkeypatch.setattr(
        ai_discovery,
        "provider_settings",
        lambda: {"provider": "gemini", "model": "test-model"},
    )
    monkeypatch.setattr(ai_discovery, "analyze_transcript_chunk", fake_discovery)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(rendering, "subtitles_filter_available", lambda: True)
    monkeypatch.setattr(rendering, "render_sequence", fake_render)

    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        project_response = client.post(
            "/api/projects",
            json={"name": "E2E pipeline", "description": "Stage 7 reliability"},
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]

        import_response = client.post(
            f"/api/projects/{project_id}/media",
            files={"file": ("pipeline.mp4", b"synthetic-video-source", "video/mp4")},
        )
        assert import_response.status_code == 201
        media = import_response.json()
        media_id = media["id"]
        source_path = database.MEDIA_DIR / f"{media_id}.mp4"
        assert source_path.read_bytes() == b"synthetic-video-source"
        assert (database.THUMBNAIL_DIR / f"{media_id}.jpg").is_file()

        transcription_response = client.post(
            f"/api/media/{media_id}/transcription",
            json={"model_name": "tiny", "language": "id"},
        )
        assert transcription_response.status_code == 202
        transcription_id = transcription_response.json()["id"]
        main_module.run_transcription_job(
            transcription_id, str(source_path), "tiny", "id"
        )

        transcript_response = client.get(f"/api/transcriptions/{transcription_id}")
        assert transcript_response.status_code == 200
        transcript = transcript_response.json()
        assert transcript["status"] == "completed"
        assert len(transcript["segments"]) == 6
        assert len(transcript["segments"][0]["words"]) == 5

        discovery_response = client.post(
            f"/api/media/{media_id}/discovery",
            json={
                "transcription_id": transcription_id,
                "mode": "auto",
                "min_duration": 30,
                "max_duration": 60,
            },
        )
        assert discovery_response.status_code == 202
        discovery_id = discovery_response.json()["id"]
        main_module.run_discovery_job(discovery_id)

        discovery_result = client.get(f"/api/discovery/{discovery_id}")
        assert discovery_result.status_code == 200
        discovery = discovery_result.json()
        assert discovery["status"] == "completed"
        assert len(discovery["candidates"]) == 1
        candidate = discovery["candidates"][0]
        assert candidate["score"] == 91
        assert candidate["end_seconds"] > candidate["start_seconds"]

        # Export the discovered candidate together with a second manually
        # selected range to exercise the multi-clip queue payload end-to-end.
        candidate_start = candidate["start_seconds"]
        candidate_end = candidate["end_seconds"]
        export_response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": candidate_start,
                "end_seconds": 95,
                "aspect_ratio": "9:16",
                "output_preset": "youtube_shorts",
                "burn_subtitles": True,
                "clip_segments": [
                    {"start": candidate_start, "end": candidate_end},
                    {"start": 65, "end": 95},
                ],
            },
        )
        assert export_response.status_code == 202
        export_id = export_response.json()["id"]
        output_path = database.EXPORT_DIR / f"clip-{export_id}.mp4"
        main_module.run_export_job(export_id, str(source_path), str(output_path))

        export_result = client.get(f"/api/exports/{export_id}")
        assert export_result.status_code == 200
        export = export_result.json()
        assert export["status"] == "completed"
        assert export["progress"] == 1
        assert export["clip_count"] == 2
        assert export["download_url"] == f"/api/exports/{export_id}/file"

        assert len(observed["clip_segments"]) == 2
        assert observed["aspect_ratio"] == "9:16"
        assert observed["quality_profile"] == "balanced"
        assert observed["subtitles"]
        assert len(observed["subtitles"]) == 30
        assert output_path.is_file()
        assert output_path.stat().st_size > 1024

        download = client.get(f"/api/exports/{export_id}/file")
        assert download.status_code == 200
        assert download.headers["content-type"] == "video/mp4"
        assert download.content == output_path.read_bytes()

        history = client.get("/api/exports")
        assert history.status_code == 200
        assert any(item["id"] == export_id and item["status"] == "completed" for item in history.json())

        with database.get_connection() as connection:
            persisted = {
                "media": connection.execute(
                    "SELECT COUNT(*) FROM media_assets WHERE id=?", (media_id,)
                ).fetchone()[0],
                "transcription": connection.execute(
                    "SELECT status FROM transcription_jobs WHERE id=?", (transcription_id,)
                ).fetchone()["status"],
                "discovery": connection.execute(
                    "SELECT status FROM discovery_jobs WHERE id=?", (discovery_id,)
                ).fetchone()["status"],
                "candidates": connection.execute(
                    "SELECT COUNT(*) FROM clip_candidates WHERE discovery_job_id=?", (discovery_id,)
                ).fetchone()[0],
                "export": connection.execute(
                    "SELECT status FROM export_jobs WHERE id=?", (export_id,)
                ).fetchone()["status"],
            }
        assert persisted == {
            "media": 1,
            "transcription": "completed",
            "discovery": "completed",
            "candidates": 1,
            "export": "completed",
        }



def test_real_media_api_import_transcribe_discover_and_export(tmp_path, monkeypatch):
    """Exercise the real import/probe/thumbnail and FFmpeg export path end to end."""
    import json
    import shutil
    import subprocess

    import pytest

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg and FFprobe are required for the real API pipeline test.")

    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "start_job_workers", lambda: None)
    monkeypatch.setattr(main_module, "start_export_worker", lambda: None)
    monkeypatch.setattr(main_module, "stop_job_workers", lambda: None)
    monkeypatch.setattr(transcription, "is_available", lambda: True)
    monkeypatch.setattr(
        transcription,
        "whisper_model_preflight",
        lambda _model: {"disk_space_known": False, "disk_space_ok": True},
    )

    def fake_transcribe(path, model_name, language, progress, cancel_check=None):
        assert path.is_file()
        assert model_name == "tiny"
        assert language == "id"
        if cancel_check:
            cancel_check()
        progress(0.8)
        return {
            "language": "id",
            "language_probability": 0.99,
            "duration": 16.0,
            "segments": [{
                "start": 0.0,
                "end": 16.0,
                "text": "Ini adalah transkrip pengujian video nyata.",
                "words": [],
            }],
        }

    monkeypatch.setattr(transcription, "transcribe_file", fake_transcribe)
    monkeypatch.setattr(ai_discovery, "provider_settings", lambda: {
        "provider": "gemini", "model": "test-model"
    })
    monkeypatch.setattr(audio_analysis, "analyze_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai_discovery, "analyze_transcript_chunk", lambda *args, **kwargs: [{
        "start_seconds": 0.5,
        "end_seconds": 15.5,
        "title": "Real media candidate",
        "hook": "Pembukaan yang jelas",
        "summary": "Kandidat untuk uji rendering nyata.",
        "rationale": "Durasi dan konteks memenuhi batas.",
        "score": 92,
        "category": "education",
    }])
    monkeypatch.setattr(main_module, "refine_candidate_boundaries", lambda *args, **kwargs: {
        "start_seconds": 0.5,
        "end_seconds": 15.5,
        "boundary_quality": 92,
        "opening_boundary": "Awal kalimat",
        "ending_boundary": "Akhir kalimat",
        "context_note": "Batas kandidat stabil",
    })

    source = tmp_path / "real-source.mp4"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=10:d=16",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=16",
            "-shortest", "-c:v", "mpeg4", "-q:v", "8", "-c:a", "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=90,
    )

    with TestClient(app) as client:
        project = client.post(
            "/api/projects",
            json={"name": "Real media E2E", "description": "FFmpeg integration"},
        )
        assert project.status_code == 201
        project_id = project.json()["id"]

        imported = client.post(
            f"/api/projects/{project_id}/media",
            files={"file": ("real-source.mp4", source.read_bytes(), "video/mp4")},
        )
        assert imported.status_code == 201, imported.text
        media = imported.json()
        media_id = media["id"]
        assert media["duration"] is not None and 15.8 <= media["duration"] <= 16.2
        assert (database.MEDIA_DIR / f"{media_id}.mp4").is_file()
        assert (database.THUMBNAIL_DIR / f"{media_id}.jpg").is_file()

        transcription_response = client.post(
            f"/api/media/{media_id}/transcription",
            json={"model_name": "tiny", "language": "id"},
        )
        assert transcription_response.status_code == 202, transcription_response.text
        transcription_id = transcription_response.json()["id"]
        main_module.run_transcription_job(
            transcription_id, str(database.MEDIA_DIR / f"{media_id}.mp4"), "tiny", "id"
        )
        transcript = client.get(f"/api/transcriptions/{transcription_id}").json()
        assert transcript["status"] == "completed"
        assert len(transcript["segments"]) == 1

        discovery_response = client.post(
            f"/api/media/{media_id}/discovery",
            json={
                "transcription_id": transcription_id,
                "mode": "auto",
                "min_duration": 15,
                "max_duration": 15,
            },
        )
        assert discovery_response.status_code == 202, discovery_response.text
        discovery_id = discovery_response.json()["id"]
        main_module.run_discovery_job(discovery_id)
        discovery = client.get(f"/api/discovery/{discovery_id}").json()
        assert discovery["status"] == "completed"
        assert len(discovery["candidates"]) == 1
        candidate = discovery["candidates"][0]
        assert candidate["start_seconds"] == 0.5
        assert candidate["end_seconds"] == 15.5

        export_response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": candidate["start_seconds"],
                "end_seconds": candidate["end_seconds"],
                "aspect_ratio": "9:16",
                "output_preset": "youtube_shorts",
                "quality_profile": "balanced",
                "frame_rate": 24,
                "burn_subtitles": False,
            },
        )
        assert export_response.status_code == 202, export_response.text
        export_id = export_response.json()["id"]
        output = database.EXPORT_DIR / f"clip-{export_id}.mp4"
        main_module.run_export_job(
            export_id,
            str(database.MEDIA_DIR / f"{media_id}.mp4"),
            str(output),
        )

        exported = client.get(f"/api/exports/{export_id}").json()
        assert exported["status"] == "completed"
        assert exported["progress"] == 1
        assert output.is_file() and output.stat().st_size > 1024

        probe = subprocess.run(
            [
                ffprobe, "-v", "error", "-show_entries",
                "stream=codec_name,codec_type,width,height:format=format_name,duration",
                "-of", "json", str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        metadata = json.loads(probe.stdout)
        video = next(item for item in metadata["streams"] if item["codec_type"] == "video")
        audio = next(item for item in metadata["streams"] if item["codec_type"] == "audio")
        assert (video["width"], video["height"]) == (1080, 1920)
        assert video["codec_name"] == "h264"
        assert audio["codec_name"] == "aac"
        assert 14.5 <= float(metadata["format"]["duration"]) <= 15.5

        download = client.get(f"/api/exports/{export_id}/file")
        assert download.status_code == 200
        assert download.content == output.read_bytes()
