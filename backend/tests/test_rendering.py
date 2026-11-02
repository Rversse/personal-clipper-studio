import json
from pathlib import Path
import uuid

from fastapi.testclient import TestClient

from app import database, rendering, main as main_module
from app.main import app


def setup_test_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(database, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(database, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setattr(main_module, "MEDIA_DIR", database.MEDIA_DIR)
    monkeypatch.setattr(main_module, "EXPORT_DIR", database.EXPORT_DIR)


def seed_media():
    project_id = str(uuid.uuid4())
    media_id = str(uuid.uuid4())
    timestamp = "2026-10-31T00:00:00+00:00"
    database.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{media_id}.mp4"
    (database.MEDIA_DIR / stored_name).write_bytes(b"source-video")
    with database.get_connection() as connection:
        connection.execute(
            "INSERT INTO projects(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
            (project_id, "Export test", "", timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO media_assets(
                id,project_id,original_name,stored_name,mime_type,file_size,duration,created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (media_id, project_id, "source.mp4", stored_name, "video/mp4", 12, 120.0, timestamp),
        )
    return media_id


def _seed_interrupted_export(job_id, output_name):
    timestamp = "2026-11-01T00:00:00+00:00"
    with database.get_connection() as connection:
        media = connection.execute("SELECT id FROM media_assets LIMIT 1").fetchone()
        if media is None:
            raise AssertionError("Test media must be seeded before creating an export job.")
        connection.execute(
            """INSERT INTO export_jobs(
                id,media_id,status,start_seconds,end_seconds,aspect_ratio,output_name,
                progress,created_at,updated_at
            ) VALUES(?,?,'processing',0,30,'9:16',?,0.65,?,?)""",
            (job_id, media["id"], output_name, timestamp, timestamp),
        )


def test_recovery_requeues_interrupted_render_and_removes_only_its_temp_files(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    database.ensure_storage()
    seed_media()
    job_id = str(uuid.uuid4())
    output_name = f"clip-{job_id}.mp4"
    destination = database.EXPORT_DIR / output_name
    _seed_interrupted_export(job_id, output_name)

    partial = destination.with_name(destination.stem + ".partial" + destination.suffix)
    part = destination.parent / f".{destination.stem}-stale-001.mp4"
    concat = destination.parent / f".{destination.stem}-stale.ffconcat"
    subtitle = destination.parent / f".{destination.stem}-stale.ass"
    unrelated = destination.parent / ".clip-another-job-stale-001.mp4"
    for path in (partial, part, concat, subtitle, unrelated):
        path.write_bytes(b"temporary")

    main_module.recover_interrupted_exports()

    with database.get_connection() as connection:
        job = connection.execute("SELECT * FROM export_jobs WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "queued"
    assert job["progress"] == 0
    assert job["encoder"] is None
    assert job["error"] is None
    for path in (partial, part, concat, subtitle):
        assert not path.exists()
    assert unrelated.exists()


def test_recovery_keeps_atomically_committed_mp4_and_marks_job_completed(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    database.ensure_storage()
    seed_media()
    job_id = str(uuid.uuid4())
    output_name = f"clip-{job_id}.mp4"
    destination = database.EXPORT_DIR / output_name
    _seed_interrupted_export(job_id, output_name)
    destination.write_bytes(bytes([0, 0, 0, 24]) + b"ftypisom" + b"x" * 1200)
    monkeypatch.setattr(main_module.rendering, "validate_mp4_output", lambda _path, expected_duration=None: {"duration": 30.0, "width": 1080, "height": 1920})

    main_module.recover_interrupted_exports()

    with database.get_connection() as connection:
        job = connection.execute("SELECT * FROM export_jobs WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "completed"
    assert job["progress"] == 1
    assert job["error"] is None
    assert destination.is_file()
    assert destination.stat().st_size > 1024


def test_real_ffmpeg_sequence_render_produces_vertical_h264_aac_mp4(tmp_path, monkeypatch):
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        import pytest
        pytest.skip("FFmpeg and FFprobe are required for the real-media integration test.")

    source = tmp_path / "synthetic-source.mp4"
    output = tmp_path / "synthetic-short.mp4"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-shortest", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac",
            str(source),
        ],
        check=True, capture_output=True, timeout=60,
    )
    monkeypatch.setattr(rendering, "available_encoder", lambda *_args: "libx264")
    result = rendering.render_sequence(
        source, output,
        [{"start": 0.25, "end": 1.75, "crop_zoom": 1, "crop_x": 0.5, "crop_y": 0.5, "audio_volume": 1}],
        "9:16", quality_profile="balanced", frame_rate=30,
    )
    assert result["encoder"] == "libx264"
    assert output.is_file() and output.stat().st_size > 1024

    probe = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries",
            "stream=codec_name,codec_type,width,height:format=duration",
            "-of", "json", str(output),
        ],
        check=True, capture_output=True, text=True, timeout=30,
    )
    metadata = json.loads(probe.stdout)
    streams = metadata["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    audio = next(stream for stream in streams if stream["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["codec_name"] == "h264"
    assert audio["codec_name"] == "aac"
    assert 1.35 <= float(metadata["format"]["duration"]) <= 1.65


def test_render_command_has_trim_crop_and_safe_audio_mapping(tmp_path):
    command = rendering.build_render_command(
        tmp_path / "source.mp4", tmp_path / "output.mp4", 5, 35, "9:16", "libx264"
    )
    assert command[command.index("-ss") + 1] == "5.000"
    assert command[command.index("-t") + 1] == "30.000"
    video_filter = command[command.index("-vf") + 1]
    assert "crop=w=1080/(1.0000):h=1920/(1.0000)" in video_filter
    assert "scale=1080:1920,setsar=1" in video_filter
    assert command[command.index("-map") + 3] == "0:a:0?"
    assert "libx264" in command
    assert "+faststart" in command


def test_render_command_rejects_invalid_range_and_ratio(tmp_path):
    for args in [
        (5, 5, "9:16"),
        (-1, 10, "9:16"),
        (0, 10, "4:3"),
    ]:
        try:
            rendering.build_render_command(
                tmp_path / "source.mp4", tmp_path / "output.mp4",
                args[0], args[1], args[2], "libx264"
            )
        except rendering.RenderError:
            pass
        else:
            raise AssertionError("Invalid render arguments must be rejected")


def test_export_api_persists_job_and_serves_completed_file(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")

    def fake_render(source, destination, start, end, ratio, progress=None, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"x" * 2048)
        if progress:
            progress(0.5)
        return {"encoder": "libx264", "output": str(destination)}

    monkeypatch.setattr(rendering, "render_sequence", lambda source, destination, segments, ratio, progress=None, **kwargs: fake_render(source, destination, segments[0]["start"], segments[-1]["end"], ratio, progress, **kwargs))
    real_run_export_job = main_module.run_export_job
    monkeypatch.setattr(main_module, "run_export_job", lambda *args: None)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={"start_seconds": 10, "end_seconds": 40, "aspect_ratio": "9:16"},
        )
        assert response.status_code == 202
        job = response.json()
        assert job["status"] == "queued"
        job_id = job["id"]
        real_run_export_job(
            job_id, str(database.MEDIA_DIR / f"{media_id}.mp4"),
            str(database.EXPORT_DIR / job["output_name"])
        )
        job = client.get(f"/api/exports/{job_id}").json()
        assert job["status"] == "completed"
        assert job["progress"] == 1
        assert job["encoder"] == "libx264"
        assert job["id"] == job_id
        download = client.get(job["download_url"])
        assert download.status_code == 200
        assert len(download.content) == 2048
        assert client.get("/api/exports").json()[0]["id"] == job["id"]


def test_export_api_rejects_range_outside_source(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={"start_seconds": 90, "end_seconds": 130, "aspect_ratio": "9:16"},
        )
        assert response.status_code == 422
        assert "durasi video sumber" in response.json()["detail"]


def test_nvenc_runtime_failure_retries_with_cpu_and_replaces_atomically(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    destination = tmp_path / "final.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(rendering.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(rendering, "available_encoder", lambda _: "h264_nvenc")
    attempts = []

    def fake_execute(command, duration, progress, control=None):
        attempts.append(command)
        if "h264_nvenc" in command:
            raise rendering.RenderError("NVENC device unavailable")
        Path(command[-1]).write_bytes(b"x" * 2048)

    monkeypatch.setattr(rendering, "_execute", fake_execute)
    monkeypatch.setattr(rendering, "validate_mp4_output", lambda _path, expected_duration=None: {"duration": expected_duration, "width": 1920, "height": 1080})
    result = rendering.render_clip(source, destination, 0, 20, "16:9")
    assert result["encoder"] == "libx264"
    assert len(attempts) == 2
    assert destination.is_file()
    assert not (tmp_path / "final.partial.mp4").exists()



def test_ass_subtitles_are_clipped_and_escaped():
    content = rendering.build_ass_content(
        [
            {"start": 8.5, "end": 12, "text": "Hello {world}\nNext line"},
            {"start": 30, "end": 35, "text": "Outside clip"},
        ],
        10, 20, "bold", "9:16",
    )
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "Fontsize" in content
    assert "Dialogue: 0,0:00:00.00,0:00:02.00" in content
    assert r"\{world\}" in content
    assert r"\NNext line" in content
    assert "Outside clip" not in content


def test_ass_subtitles_reject_invalid_timestamps_and_preset():
    for segments, preset in [
        ([{"start": 5, "end": 5, "text": "bad"}], "clean"),
        ([{"start": float("nan"), "end": 8, "text": "bad"}], "clean"),
        ([], "unknown"),
    ]:
        try:
            rendering.build_ass_content(segments, 0, 10, preset)
        except rendering.RenderError:
            pass
        else:
            raise AssertionError("Invalid subtitle data must be rejected")


def test_render_command_adds_subtitle_filter(tmp_path):
    subtitle = tmp_path / "captions.ass"
    command = rendering.build_render_command(
        tmp_path / "source.mp4", tmp_path / "output.mp4", 0, 10,
        "9:16", "libx264", subtitle_path=subtitle, subtitle_preset="highlight",
    )
    video_filter = command[command.index("-vf") + 1]
    assert "subtitles=filename=" in video_filter
    assert "captions.ass" in video_filter
    assert "PrimaryColour=&H0000FFFF" in video_filter


def test_render_subtitle_requires_libass_and_cleans_temp_file(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    destination = tmp_path / "final.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(rendering.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(rendering, "available_encoder", lambda _: "libx264")
    monkeypatch.setattr(rendering, "subtitles_filter_available", lambda _: True)
    attempts = []

    def fake_execute(command, duration, progress, control=None):
        attempts.append(command)
        Path(command[-1]).write_bytes(b"x" * 2048)

    monkeypatch.setattr(rendering, "_execute", fake_execute)
    monkeypatch.setattr(rendering, "validate_mp4_output", lambda _path, expected_duration=None: {"duration": expected_duration, "width": 1080, "height": 1920})
    rendering.render_clip(
        source, destination, 0, 10, "9:16",
        subtitle_segments=[{"start": 1, "end": 3, "text": "Caption"}],
        subtitle_preset="clean",
    )
    assert len(attempts) == 1
    assert "subtitles=filename=" in attempts[0][attempts[0].index("-vf") + 1]
    assert not list(tmp_path.glob("*.ass"))
    assert not list(tmp_path.glob(".*.ass"))

    monkeypatch.setattr(rendering, "subtitles_filter_available", lambda _: False)
    try:
        rendering.render_clip(
            source, destination, 0, 10, "9:16",
            subtitle_segments=[{"start": 1, "end": 3, "text": "Caption"}],
        )
    except rendering.RenderError as error:
        assert "libass" in str(error)
    else:
        raise AssertionError("Missing libass must fail explicitly")



def test_export_api_persists_subtitle_payload_for_background_job(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(rendering, "subtitles_filter_available", lambda: True)
    monkeypatch.setattr(main_module, "run_export_job", lambda *args: None)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 10,
                "end_seconds": 40,
                "aspect_ratio": "9:16",
                "burn_subtitles": True,
                "subtitle_preset": "highlight",
                "subtitle_style": {"font_size": 74, "vertical_position": 14, "outline_width": 5, "background_opacity": 55},
                "subtitle_segments": [
                    {"start": 12, "end": 14, "text": "Edited caption"}
                ],
            },
        )
        assert response.status_code == 202
        job_id = response.json()["id"]
        with database.get_connection() as connection:
            row = connection.execute(
                "SELECT subtitle_payload,subtitle_preset,subtitle_style FROM export_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        import json
        assert json.loads(row["subtitle_payload"]) == [
            {"start": 12.0, "end": 14.0, "text": "Edited caption"}
        ]
        assert row["subtitle_preset"] == "highlight"
        assert json.loads(row["subtitle_style"]) == {"font_size": 74, "vertical_position": 14, "outline_width": 5.0, "background_opacity": 55}


def test_export_api_rejects_burn_in_without_segments(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(rendering, "subtitles_filter_available", lambda: True)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 10,
                "end_seconds": 40,
                "burn_subtitles": True,
                "subtitle_segments": [],
            },
        )
        assert response.status_code == 422
        assert "Tidak ada segmen subtitle" in response.json()["detail"]



def test_render_sequence_renders_in_order_and_cleans_intermediates(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    destination = tmp_path / "final.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(rendering.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    calls = []

    def fake_render(source_path, output_path, start, end, ratio, progress=None, **kwargs):
        calls.append((start, end, kwargs.copy()))
        assert kwargs["subtitle_style"] == {"font_size": 68, "vertical_position": 10, "outline_width": 3.5, "background_opacity": 35}
        output_path.write_bytes(b"x" * 2048)
        if progress:
            progress(1.0)
        return {"encoder": "libx264", "output": str(output_path)}

    def fake_concat(command, duration, progress, control=None):
        assert command[command.index("-f") + 1] == "concat"
        list_path = Path(command[command.index("-i") + 1])
        entries = list_path.read_text(encoding="utf-8").splitlines()
        assert len(entries) == 2
        Path(command[-1]).write_bytes(b"y" * 4096)

    monkeypatch.setattr(rendering, "render_clip", fake_render)
    monkeypatch.setattr(rendering, "_execute", fake_concat)
    monkeypatch.setattr(rendering, "validate_mp4_output", lambda _path, expected_duration=None: {"duration": expected_duration, "width": 1080, "height": 1920})
    progress_values = []
    subtitle_style = {"font_size": 68, "vertical_position": 10, "outline_width": 3.5, "background_opacity": 35}
    result = rendering.render_sequence(
        source, destination,
        [
            {"start": 30, "end": 40, "crop_zoom": 2.0, "crop_x": 0.2, "crop_y": 0.7},
            {"start": 5, "end": 15, "crop_zoom": 1.25, "crop_x": 0.8, "crop_y": 0.3},
        ],
        "9:16", progress=progress_values.append, subtitle_style=subtitle_style,
    )
    assert [(item[0], item[1]) for item in calls] == [(30, 40), (5, 15)]
    assert [(item[2]["crop_zoom"], item[2]["crop_x"], item[2]["crop_y"]) for item in calls] == [
        (2.0, 0.2, 0.7), (1.25, 0.8, 0.3)
    ]
    assert result["encoder"] == "libx264"
    assert destination.is_file()
    assert progress_values[-1] == 1.0
    assert not list(tmp_path.glob(".*-part-*.mp4"))
    assert not list(tmp_path.glob("*.ffconcat"))


def test_render_sequence_rejects_invalid_clip_ranges(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    for clips in [[], [{"start": 2, "end": 2}], [{"start": -1, "end": 2}]]:
        try:
            rendering.render_sequence(source, tmp_path / "out.mp4", clips, "9:16")
        except rendering.RenderError:
            pass
        else:
            raise AssertionError("Invalid sequence must be rejected")



def test_export_api_persists_ordered_clip_sequence(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(main_module, "run_export_job", lambda *args: None)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 5,
                "end_seconds": 40,
                "aspect_ratio": "9:16",
                "clip_segments": [
                    {"start": 30, "end": 40},
                    {"start": 5, "end": 15},
                ],
            },
        )
        assert response.status_code == 202
        job = response.json()
        assert job["clip_count"] == 2
        with database.get_connection() as connection:
            row = connection.execute(
                "SELECT clip_segments,start_seconds,end_seconds FROM export_jobs WHERE id=?",
                (job["id"],),
            ).fetchone()
        import json
        assert json.loads(row["clip_segments"]) == [
            {"start": 30.0, "end": 40.0, "crop_zoom": 1.0, "crop_x": 0.5, "crop_y": 0.5, "audio_volume": 1.0},
            {"start": 5.0, "end": 15.0, "crop_zoom": 1.0, "crop_x": 0.5, "crop_y": 0.5, "audio_volume": 1.0},
        ]
        assert row["start_seconds"] == 5
        assert row["end_seconds"] == 40


def test_export_api_rejects_sequence_segment_outside_source(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 5,
                "end_seconds": 40,
                "clip_segments": [{"start": 100, "end": 130}],
            },
        )
        assert response.status_code == 422
        assert "melewati durasi" in response.json()["detail"]



def test_render_command_applies_zoom_and_focal_position(tmp_path):
    command = rendering.build_render_command(
        tmp_path / "source.mp4", tmp_path / "output.mp4", 0, 10,
        "9:16", "libx264", crop_zoom=1.75, crop_x=0.2, crop_y=0.8,
    )
    video_filter = command[command.index("-vf") + 1]
    assert "crop=w=1080/(1.7500):h=1920/(1.7500)" in video_filter
    assert ")*0.2000" in video_filter
    assert ")*0.8000" in video_filter


def test_render_command_rejects_invalid_crop_values(tmp_path):
    for zoom, x, y in [(0.9, 0.5, 0.5), (3.1, 0.5, 0.5), (1.0, -0.1, 0.5), (1.0, 0.5, 1.1)]:
        try:
            rendering.build_render_command(
                tmp_path / "source.mp4", tmp_path / "out.mp4",
                0, 10, "9:16", "libx264", crop_zoom=zoom, crop_x=x, crop_y=y,
            )
        except rendering.RenderError:
            pass
        else:
            raise AssertionError("Invalid crop values must be rejected")


def test_export_api_persists_crop_settings(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(main_module, "run_export_job", lambda *args: None)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 5, "end_seconds": 40,
                "crop_zoom": 1.8, "crop_x": 0.25, "crop_y": 0.7,
            },
        )
        assert response.status_code == 202
        with database.get_connection() as connection:
            row = connection.execute(
                "SELECT crop_zoom,crop_x,crop_y FROM export_jobs WHERE id=?",
                (response.json()["id"],),
            ).fetchone()
        assert row["crop_zoom"] == 1.8
        assert row["crop_x"] == 0.25
        assert row["crop_y"] == 0.7
        assert response.json()["crop_zoom"] == 1.8
        assert response.json()["crop_x"] == 0.25
        assert response.json()["crop_y"] == 0.7


def test_export_api_rejects_crop_settings_outside_supported_range(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={"start_seconds": 5, "end_seconds": 40, "crop_zoom": 3.5},
        )
        assert response.status_code == 422


def test_subtitle_style_controls_map_to_ffmpeg_force_style(tmp_path):
    command = rendering.build_render_command(
        tmp_path / "source.mp4", tmp_path / "output.mp4", 0, 10,
        "9:16", "libx264", subtitle_path=tmp_path / "captions.ass",
        subtitle_preset="clean",
        subtitle_style={
            "font_size": 72, "vertical_position": 12,
            "outline_width": 4.5, "background_opacity": 60,
        },
    )
    video_filter = command[command.index("-vf") + 1]
    assert "FontSize=72" in video_filter
    assert "BorderStyle=3" in video_filter
    assert "Outline=4.5" in video_filter
    assert "MarginV=230" in video_filter
    assert "OutlineColour=&H66000000" in video_filter


def test_subtitle_style_rejects_out_of_range_values(tmp_path):
    invalid_styles = [
        {"font_size": 23}, {"font_size": 121},
        {"vertical_position": 4}, {"vertical_position": 31},
        {"outline_width": -0.1}, {"outline_width": 8.1},
        {"background_opacity": -1}, {"background_opacity": 91},
        {"outline_width": float("nan")},
    ]
    for style in invalid_styles:
        try:
            rendering.build_render_command(
                tmp_path / "source.mp4", tmp_path / "out.mp4",
                0, 10, "9:16", "libx264",
                subtitle_path=tmp_path / "captions.ass",
                subtitle_style=style,
            )
        except rendering.RenderError:
            pass
        else:
            raise AssertionError("Out-of-range subtitle style must be rejected")


def test_execute_reports_live_progress_while_ffmpeg_runs(monkeypatch):
    import io

    class FakeProcess:
        def __init__(self, **kwargs):
            self.stdout = io.StringIO(
                "frame=10\nout_time_ms=200000\nprogress=continue\n"
                "frame=25\nout_time_ms=650000\nprogress=continue\n"
                "out_time_ms=1000000\nprogress=end\n"
            )
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

        def communicate(self, timeout=None):
            return ("", "")

    monkeypatch.setattr(rendering.subprocess, "Popen", lambda *args, **kwargs: FakeProcess(**kwargs))
    updates = []
    rendering._execute(["ffmpeg", "-progress", "pipe:1"], 2.0, updates.append)
    assert updates == [0.1, 0.325, 0.5]



def test_render_control_kills_attached_process():
    class FakeProcess:
        def __init__(self):
            self.returncode = None
            self.terminated = False
            self.killed = False
        def poll(self):
            return self.returncode
        def terminate(self):
            self.terminated = True
            self.returncode = -15
        def wait(self, timeout=None):
            return self.returncode
        def kill(self):
            self.killed = True
            self.returncode = -9

    control = rendering.RenderControl()
    process = FakeProcess()
    control.attach(process)
    control.cancel()
    assert process.terminated
    assert control._cancelled.is_set()
    try:
        control.check()
    except rendering.RenderCancelled:
        pass
    else:
        raise AssertionError("Cancelled control must reject continued work")


def test_cancel_all_controls_terminates_every_active_render():
    class FakeProcess:
        def __init__(self):
            self.returncode = None
            self.terminated = False
        def poll(self):
            return self.returncode
        def terminate(self):
            self.terminated = True
            self.returncode = -15
        def wait(self, timeout=None):
            return self.returncode
        def kill(self):
            self.returncode = -9

    controls = [rendering.register_control(f"shutdown-{index}") for index in range(2)]
    processes = [FakeProcess() for _ in controls]
    try:
        for control, process in zip(controls, processes):
            control.attach(process)

        assert rendering.cancel_all_controls() == 2
        assert all(process.terminated for process in processes)
        assert all(control._cancelled.is_set() for control in controls)
    finally:
        for index in range(len(controls)):
            rendering.unregister_control(f"shutdown-{index}")


def test_execute_honors_pre_cancelled_control(monkeypatch):
    control = rendering.RenderControl()
    control.cancel()
    monkeypatch.setattr(
        rendering.subprocess, "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("FFmpeg must not start")),
    )
    try:
        rendering._execute(["ffmpeg"], 10, None, control)
    except rendering.RenderCancelled:
        pass
    else:
        raise AssertionError("Pre-cancelled render must stop before spawning FFmpeg")


def test_cancel_queued_export_changes_terminal_state(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "_EXPORT_WORKER_WAKE", type("NoWake", (), {"set": lambda self: None, "clear": lambda self: None, "wait": lambda self, timeout=None: False})())
    monkeypatch.setattr(main_module, "start_export_worker", lambda: None)
    monkeypatch.setattr(main_module, "_EXPORT_WORKER_WAKE", type("NoWake", (), {"set": lambda self: None})())
    monkeypatch.setattr(main_module, "start_export_worker", lambda: None)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    with TestClient(app) as client:
        media_id = seed_media()
        monkeypatch.setattr(main_module, "_EXPORT_WORKER_WAKE", type("NoWake", (), {"set": lambda self: None})())
        monkeypatch.setattr(main_module, "start_export_worker", lambda: None)
        monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={"start_seconds": 5, "end_seconds": 20},
        )
        assert response.status_code == 202
        job_id = response.json()["id"]
        cancelled = client.post(f"/api/exports/{job_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert client.post(f"/api/exports/{job_id}/cancel").status_code == 409


def test_export_dequeued_during_shutdown_is_requeued_before_render(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "start_export_worker", lambda: None)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")

    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={"start_seconds": 5, "end_seconds": 20},
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        main_module._EXPORT_WORKER_STOP.set()
        main_module.run_export_job(
            job_id,
            str(database.MEDIA_DIR / f"{media_id}.mp4"),
            str(database.EXPORT_DIR / response.json()["output_name"]),
        )

        with database.get_connection() as connection:
            job = connection.execute(
                "SELECT status,error FROM export_jobs WHERE id=?", (job_id,)
            ).fetchone()
        assert job["status"] == "queued"
        assert job["error"] is None
        assert rendering.get_control(job_id) is None


def test_active_export_interruption_is_requeued_for_next_launch(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "start_export_worker", lambda: None)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")

    def interrupt_during_render(*args, **kwargs):
        kwargs["control"].interrupt()
        kwargs["control"].check()

    monkeypatch.setattr(rendering, "render_sequence", interrupt_during_render)

    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={"start_seconds": 5, "end_seconds": 20},
        )
        assert response.status_code == 202
        job_id = response.json()["id"]
        destination = database.EXPORT_DIR / response.json()["output_name"]

        main_module.run_export_job(
            job_id,
            str(database.MEDIA_DIR / f"{media_id}.mp4"),
            str(destination),
        )

        with database.get_connection() as connection:
            job = connection.execute(
                "SELECT status,progress,encoder,error FROM export_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        assert job["status"] == "queued"
        assert job["progress"] == 0
        assert job["encoder"] is None
        assert job["error"] is None
        assert not destination.exists()
        assert rendering.get_control(job_id) is None


def test_validated_output_survives_shutdown_after_atomic_publish(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "start_export_worker", lambda: None)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(main_module, "_is_completed_export", lambda path: path.is_file())

    def publish_then_interrupt(source, destination, *args, **kwargs):
        destination = Path(destination)
        destination.write_bytes(b"validated-final-mp4")
        kwargs["control"].interrupt()
        return {"encoder": "libx264", "output": str(destination)}

    monkeypatch.setattr(rendering, "render_sequence", publish_then_interrupt)

    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={"start_seconds": 5, "end_seconds": 20},
        )
        assert response.status_code == 202
        job_id = response.json()["id"]
        destination = database.EXPORT_DIR / response.json()["output_name"]

        main_module.run_export_job(
            job_id,
            str(database.MEDIA_DIR / f"{media_id}.mp4"),
            str(destination),
        )

        with database.get_connection() as connection:
            job = connection.execute(
                "SELECT status,progress,error FROM export_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        assert job["status"] == "completed"
        assert job["progress"] == 1
        assert job["error"] is None
        assert destination.read_bytes() == b"validated-final-mp4"
        assert rendering.get_control(job_id) is None


def test_execute_terminates_running_process_when_cancelled(monkeypatch):
    import io
    import threading
    import time

    spawned = threading.Event()

    class FakeProcess:
        def __init__(self, **kwargs):
            self.stdout = io.StringIO("")
            self.returncode = None
            self.terminated = False
            spawned.set()
        def poll(self):
            return self.returncode
        def wait(self, timeout=None):
            if self.returncode is None and timeout is not None:
                time.sleep(min(timeout, 0.01))
            return self.returncode
        def terminate(self):
            self.terminated = True
            self.returncode = -15
        def kill(self):
            self.returncode = -9

    process_box = []
    def spawn(*args, **kwargs):
        process = FakeProcess(**kwargs)
        process_box.append(process)
        return process

    monkeypatch.setattr(rendering.subprocess, "Popen", spawn)
    control = rendering.RenderControl()
    errors = []
    worker = threading.Thread(
        target=lambda: _capture_render_error(errors, lambda: rendering._execute(["ffmpeg"], 10, None, control))
    )
    worker.start()
    assert spawned.wait(timeout=2)
    control.cancel()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert process_box[0].terminated
    assert errors and isinstance(errors[0], rendering.RenderCancelled)


def _capture_render_error(errors, operation):
    try:
        operation()
    except Exception as error:
        errors.append(error)


def test_export_api_persists_per_clip_crop_and_uses_global_fallback(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(main_module, "run_export_job", lambda *args: None)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 5, "end_seconds": 40,
                "crop_zoom": 1.4, "crop_x": 0.35, "crop_y": 0.65,
                "clip_segments": [
                    {"start": 30, "end": 40, "crop_zoom": 2.2, "crop_x": 0.1, "crop_y": 0.9},
                    {"start": 5, "end": 15},
                ],
            },
        )
        assert response.status_code == 202
        with database.get_connection() as connection:
            row = connection.execute(
                "SELECT clip_segments FROM export_jobs WHERE id=?", (response.json()["id"],)
            ).fetchone()
        import json
        assert json.loads(row["clip_segments"]) == [
            {"start": 30.0, "end": 40.0, "crop_zoom": 2.2, "crop_x": 0.1, "crop_y": 0.9, "audio_volume": 1.0},
            {"start": 5.0, "end": 15.0, "crop_zoom": 1.4, "crop_x": 0.35, "crop_y": 0.65, "audio_volume": 1.0},
        ]


def test_render_sequence_rejects_invalid_per_clip_crop(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    try:
        rendering.render_sequence(
            source, Path("unused-output.mp4"),
            [{"start": 0, "end": 5, "crop_zoom": 3.5}], "9:16",
        )
    except rendering.RenderError as error:
        assert "crop" in str(error).lower()
    else:
        raise AssertionError("Out-of-range per-clip crop must be rejected")


def test_render_command_applies_audio_gain_and_mute_and_validates_range(tmp_path):
    base = dict(
        source=tmp_path / "source.mp4",
        destination=tmp_path / "output.mp4",
        start_seconds=0,
        end_seconds=10,
        aspect_ratio="9:16",
        encoder="libx264",
    )
    command = rendering.build_render_command(**base, audio_volume=0.35)
    assert command[command.index("-af") + 1] == "volume=0.3500"

    muted = rendering.build_render_command(**base, audio_volume=0.0)
    assert muted[muted.index("-af") + 1] == "volume=0.0000"

    unchanged = rendering.build_render_command(**base, audio_volume=1.0)
    assert "-af" not in unchanged

    for volume in (-0.01, 2.01, float("nan")):
        try:
            rendering.build_render_command(**base, audio_volume=volume)
        except rendering.RenderError as error:
            assert "volume" in str(error).lower()
        else:
            raise AssertionError("Invalid audio gain must be rejected")


def test_render_sequence_applies_distinct_audio_gain_per_clip(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    destination = tmp_path / "final.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(rendering.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    calls = []

    def fake_render(source_path, output_path, start, end, ratio, progress=None, **kwargs):
        calls.append(kwargs["audio_volume"])
        output_path.write_bytes(b"x" * 2048)
        return {"encoder": "libx264", "output": str(output_path)}

    def fake_concat(command, duration, progress, control=None):
        Path(command[-1]).write_bytes(b"y" * 4096)

    monkeypatch.setattr(rendering, "render_clip", fake_render)
    monkeypatch.setattr(rendering, "_execute", fake_concat)
    monkeypatch.setattr(rendering, "validate_mp4_output", lambda _path, expected_duration=None: {"duration": expected_duration, "width": 1080, "height": 1920})
    rendering.render_sequence(
        source, destination,
        [
            {"start": 0, "end": 5, "audio_volume": 0.25},
            {"start": 10, "end": 15, "audio_volume": 0.0},
        ],
        "9:16",
    )
    assert calls == [0.25, 0.0]


def test_render_sequence_rejects_invalid_per_clip_audio_gain(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    for volume in (-0.1, 2.1, float("nan")):
        try:
            rendering.render_sequence(
                source, tmp_path / "out.mp4",
                [{"start": 0, "end": 5, "audio_volume": volume}],
                "9:16",
            )
        except rendering.RenderError as error:
            assert "volume" in str(error).lower()
        else:
            raise AssertionError("Invalid per-clip audio gain must be rejected")


def test_export_api_persists_per_clip_audio_gain_and_global_fallback(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(main_module, "run_export_job", lambda *args: None)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 0, "end_seconds": 20,
                "audio_volume": 0.6,
                "clip_segments": [
                    {"start": 0, "end": 5, "audio_volume": 0.25},
                    {"start": 10, "end": 20},
                ],
            },
        )
        assert response.status_code == 202
        with database.get_connection() as connection:
            row = connection.execute(
                "SELECT clip_segments FROM export_jobs WHERE id=?", (response.json()["id"],)
            ).fetchone()
        import json
        segments = json.loads(row["clip_segments"])
        assert [segment["audio_volume"] for segment in segments] == [0.25, 0.6]


def test_export_api_rejects_audio_gain_outside_supported_range(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    with TestClient(app) as client:
        media_id = seed_media()
        for volume in (-0.1, 2.1):
            response = client.post(
                f"/api/media/{media_id}/exports",
                json={
                    "start_seconds": 0, "end_seconds": 10,
                    "audio_volume": volume,
                },
            )
            assert response.status_code == 422


def test_export_api_rejects_edited_caption_outside_source_duration(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(rendering, "subtitles_filter_available", lambda: True)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 0, "end_seconds": 20,
                "burn_subtitles": True,
                "subtitle_segments": [{"start": 0, "end": 130, "text": "too long"}],
            },
        )
        assert response.status_code == 422
        assert "subtitle" in response.json()["detail"].lower()


def test_render_command_applies_quality_frame_rate_and_loudness_options(tmp_path):
    base = dict(
        source=tmp_path / "source.mp4",
        destination=tmp_path / "output.mp4",
        start_seconds=0,
        end_seconds=10,
        aspect_ratio="9:16",
        encoder="libx264",
    )
    high = rendering.build_render_command(
        **base, quality_profile="high", frame_rate=60, normalize_audio=True
    )
    assert high[high.index("-vf") + 1].endswith("setsar=1,fps=60")
    assert high[high.index("-preset") + 1] == "slow"
    assert high[high.index("-crf") + 1] == "18"
    assert high[high.index("-af") + 1] == "loudnorm=I=-16:TP=-1.5:LRA=11"

    combined = rendering.build_render_command(
        **base, audio_volume=0.5, normalize_audio=True,
        quality_profile="maximum", frame_rate=24,
    )
    assert combined[combined.index("-af") + 1] == "volume=0.5000,loudnorm=I=-16:TP=-1.5:LRA=11"
    assert combined[combined.index("-preset") + 1] == "slower"
    assert combined[combined.index("-crf") + 1] == "15"
    assert combined[combined.index("-vf") + 1].endswith("setsar=1,fps=24")

    source_fps = rendering.build_render_command(**base, frame_rate=None)
    assert not source_fps[ source_fps.index("-vf") + 1 ].endswith(",fps=30")


def test_render_command_rejects_unsupported_quality_and_frame_rate(tmp_path):
    base = dict(
        source=tmp_path / "source.mp4",
        destination=tmp_path / "output.mp4",
        start_seconds=0,
        end_seconds=10,
        aspect_ratio="9:16",
        encoder="libx264",
    )
    for options in [{"quality_profile": "ultra"}, {"frame_rate": 25}]:
        try:
            rendering.build_render_command(**base, **options)
        except rendering.RenderError:
            pass
        else:
            raise AssertionError("Unsupported rendering options must be rejected")


def test_render_sequence_propagates_quality_frame_rate_and_audio_normalization(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    destination = tmp_path / "final.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(rendering.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    calls = []

    def fake_render(source_path, output_path, start, end, ratio, progress=None, **kwargs):
        calls.append(kwargs.copy())
        output_path.write_bytes(b"x" * 2048)
        return {"encoder": "libx264", "output": str(output_path)}

    def fake_concat(command, duration, progress, control=None):
        Path(command[-1]).write_bytes(b"y" * 4096)

    monkeypatch.setattr(rendering, "render_clip", fake_render)
    monkeypatch.setattr(rendering, "_execute", fake_concat)
    monkeypatch.setattr(rendering, "validate_mp4_output", lambda _path, expected_duration=None: {"duration": expected_duration, "width": 1080, "height": 1920})
    rendering.render_sequence(
        source, destination, [{"start": 0, "end": 5}], "9:16",
        quality_profile="high", frame_rate=60, normalize_audio=True,
    )
    assert calls[0]["quality_profile"] == "high"
    assert calls[0]["frame_rate"] == 60
    assert calls[0]["normalize_audio"] is True


def test_export_api_persists_advanced_render_settings_and_returns_them(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(main_module, "run_export_job", lambda *args: None)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 5, "end_seconds": 30, "aspect_ratio": "9:16",
                "output_preset": "instagram_reels",
                "quality_profile": "maximum", "frame_rate": 60, "normalize_audio": True,
            },
        )
        assert response.status_code == 202
        payload = response.json()
        assert payload["output_preset"] == "instagram_reels"
        assert payload["quality_profile"] == "maximum"
        assert payload["frame_rate"] == 60
        assert payload["normalize_audio"] is True
        with database.get_connection() as connection:
            row = connection.execute(
                "SELECT render_settings FROM export_jobs WHERE id=?", (payload["id"],)
            ).fetchone()
        import json
        assert json.loads(row["render_settings"]) == {
            "output_preset": "instagram_reels", "quality_profile": "maximum",
            "frame_rate": 60, "normalize_audio": True,
        }


def test_export_api_rejects_platform_preset_with_mismatched_aspect_ratio(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 5, "end_seconds": 30, "aspect_ratio": "1:1",
                "output_preset": "youtube_shorts",
            },
        )
        assert response.status_code == 422
        assert "Rasio aspek" in response.json()["detail"]


def test_nvenc_quality_profile_maps_to_encoder_specific_options(tmp_path):
    command = rendering.build_render_command(
        tmp_path / "source.mp4", tmp_path / "output.mp4", 0, 10,
        "9:16", "h264_nvenc", quality_profile="high", frame_rate=30,
    )
    assert command[command.index("-preset") + 1] == "p6"
    assert command[command.index("-cq") + 1] == "20"


def test_export_api_accepts_legacy_custom_aspect_without_platform_preset(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(main_module, "run_export_job", lambda *args: None)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={"start_seconds": 5, "end_seconds": 30, "aspect_ratio": "1:1"},
        )
        assert response.status_code == 202
        assert response.json()["output_preset"] == "custom"


def test_export_worker_uses_persisted_render_settings(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    real_run_export_job = main_module.run_export_job
    monkeypatch.setattr(main_module, "run_export_job", lambda *args: None)
    captured = {}

    def fake_render(source, destination, segments, ratio, **kwargs):
        captured.update(kwargs)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"x" * 2048)
        return {"encoder": "libx264", "output": str(destination)}

    monkeypatch.setattr(rendering, "render_sequence", fake_render)
    with TestClient(app) as client:
        media_id = seed_media()
        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 5, "end_seconds": 30, "aspect_ratio": "9:16",
                "output_preset": "youtube_shorts", "quality_profile": "high",
                "frame_rate": 60, "normalize_audio": True,
            },
        )
        assert response.status_code == 202
        job = response.json()
        real_run_export_job(
            job["id"], str(database.MEDIA_DIR / f"{media_id}.mp4"),
            str(database.EXPORT_DIR / job["output_name"])
        )
    assert captured["quality_profile"] == "high"
    assert captured["frame_rate"] == 60
    assert captured["normalize_audio"] is True



def test_auto_subtitles_fall_back_to_segment_when_word_timestamps_are_partial(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(rendering, "subtitles_filter_available", lambda: True)
    monkeypatch.setattr(main_module, "run_export_job", lambda *args: None)

    with TestClient(app) as client:
        media_id = seed_media()
        timestamp = main_module.now_iso()
        transcription_id = str(uuid.uuid4())
        with database.get_connection() as connection:
            connection.execute(
                """INSERT INTO transcription_jobs(
                    id,media_id,status,model_name,created_at,updated_at
                ) VALUES(?,?,'completed','tiny',?,?)""",
                (transcription_id, media_id, timestamp, timestamp),
            )
            segment_cursor = connection.execute(
                """INSERT INTO transcript_segments(job_id,start,end,text)
                   VALUES(?,?,?,?)""",
                (transcription_id, 2.0, 4.0, "satu dua tiga"),
            )
            segment_id = segment_cursor.lastrowid
            connection.executemany(
                """INSERT INTO transcript_words(
                    job_id,segment_id,word_index,start,end,text,probability
                ) VALUES(?,?,?,?,?,?,?)""",
                [
                    (transcription_id, segment_id, 0, 2.0, 2.4, "satu", 0.9),
                    # The missing "dua" must force segment-level fallback.
                    (transcription_id, segment_id, 1, 3.2, 3.8, "tiga", 0.9),
                ],
            )

        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 0,
                "end_seconds": 10,
                "burn_subtitles": True,
                "subtitle_segments": [],
            },
        )
        assert response.status_code == 202
        with database.get_connection() as connection:
            row = connection.execute(
                "SELECT subtitle_payload FROM export_jobs WHERE id=?",
                (response.json()["id"],),
            ).fetchone()
        saved = json.loads(row["subtitle_payload"])
        assert saved == [{"start": 2.0, "end": 4.0, "text": "satu dua tiga"}]


def test_auto_subtitles_use_word_events_when_text_matches_segment(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    monkeypatch.setattr(rendering, "available_encoder", lambda: "libx264")
    monkeypatch.setattr(rendering, "subtitles_filter_available", lambda: True)
    monkeypatch.setattr(main_module, "run_export_job", lambda *args: None)

    with TestClient(app) as client:
        media_id = seed_media()
        timestamp = main_module.now_iso()
        transcription_id = str(uuid.uuid4())
        with database.get_connection() as connection:
            connection.execute(
                """INSERT INTO transcription_jobs(
                    id,media_id,status,model_name,created_at,updated_at
                ) VALUES(?,?,'completed','tiny',?,?)""",
                (transcription_id, media_id, timestamp, timestamp),
            )
            segment_cursor = connection.execute(
                """INSERT INTO transcript_segments(job_id,start,end,text)
                   VALUES(?,?,?,?)""",
                (transcription_id, 2.0, 4.0, "Satu, dua!"),
            )
            segment_id = segment_cursor.lastrowid
            connection.executemany(
                """INSERT INTO transcript_words(
                    job_id,segment_id,word_index,start,end,text,probability
                ) VALUES(?,?,?,?,?,?,?)""",
                [
                    (transcription_id, segment_id, 0, 2.0, 2.4, "Satu,", 0.9),
                    (transcription_id, segment_id, 1, 2.5, 3.0, "dua!", 0.9),
                ],
            )

        response = client.post(
            f"/api/media/{media_id}/exports",
            json={
                "start_seconds": 0,
                "end_seconds": 10,
                "burn_subtitles": True,
                "subtitle_segments": [],
            },
        )
        assert response.status_code == 202
        with database.get_connection() as connection:
            row = connection.execute(
                "SELECT subtitle_payload FROM export_jobs WHERE id=?",
                (response.json()["id"],),
            ).fetchone()
        saved = json.loads(row["subtitle_payload"])
        assert saved == [
            {"start": 2.0, "end": 2.4, "text": "Satu,"},
            {"start": 2.5, "end": 3.0, "text": "dua!"},
        ]




def test_escape_filter_path_handles_windows_drive_and_special_characters(monkeypatch, tmp_path):
    class WindowsResolvedPath:
        def __str__(self):
            return r"C:\Users\Editor\A, [take]'s subtitle.ass"

    monkeypatch.setattr(Path, "resolve", lambda _self: WindowsResolvedPath())

    escaped = rendering._escape_filter_path(tmp_path / "ignored.ass")

    assert escaped == r"C\:/Users/Editor/A\, \[take\]\'s subtitle.ass"


def test_mp4_validator_rejects_file_without_video_stream(tmp_path, monkeypatch):
    output = tmp_path / "broken.mp4"
    output.write_bytes(b"x" * 2048)
    monkeypatch.setattr(rendering.shutil, "which", lambda name: "/usr/bin/ffprobe" if name == "ffprobe" else None)
    monkeypatch.setattr(
        rendering.subprocess, "run",
        lambda *args, **kwargs: rendering.subprocess.CompletedProcess(
            args[0], 0, stdout=json.dumps({
                "format": {"format_name": "mov,mp4,m4a", "duration": "10.0"},
                "streams": [{"codec_type": "audio"}],
            }), stderr="",
        ),
    )
    try:
        rendering.validate_mp4_output(output, expected_duration=10)
    except rendering.RenderError as error:
        assert "video" in str(error).lower() or "metadata" in str(error).lower()
    else:
        raise AssertionError("An MP4 without a video stream must be rejected")


def test_mp4_validator_rejects_duration_mismatch(tmp_path, monkeypatch):
    output = tmp_path / "truncated.mp4"
    output.write_bytes(b"x" * 2048)
    monkeypatch.setattr(rendering.shutil, "which", lambda name: "/usr/bin/ffprobe" if name == "ffprobe" else None)
    monkeypatch.setattr(
        rendering.subprocess, "run",
        lambda *args, **kwargs: rendering.subprocess.CompletedProcess(
            args[0], 0, stdout=json.dumps({
                "format": {"format_name": "mov,mp4,m4a", "duration": "2.0"},
                "streams": [{"codec_type": "video", "width": 1080, "height": 1920}],
            }), stderr="",
        ),
    )
    try:
        rendering.validate_mp4_output(output, expected_duration=30)
    except rendering.RenderError as error:
        assert "Durasi" in str(error)
    else:
        raise AssertionError("A truncated output must fail duration validation")


def test_recovery_requeues_mp4_with_valid_signature_but_invalid_metadata(tmp_path, monkeypatch):
    setup_test_storage(tmp_path, monkeypatch)
    database.ensure_storage()
    seed_media()
    job_id = str(uuid.uuid4())
    output_name = f"clip-{job_id}.mp4"
    destination = database.EXPORT_DIR / output_name
    _seed_interrupted_export(job_id, output_name)
    destination.write_bytes(bytes([0, 0, 0, 24]) + b"ftypisom" + b"x" * 1200)
    monkeypatch.setattr(
        main_module.rendering, "validate_mp4_output",
        lambda _path, expected_duration=None: (_ for _ in ()).throw(rendering.RenderError("invalid MP4")),
    )

    main_module.recover_interrupted_exports()

    with database.get_connection() as connection:
        job = connection.execute("SELECT status,progress,error FROM export_jobs WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "queued"
    assert job["progress"] == 0
    assert job["error"] is None
    assert not destination.exists()


def test_render_clip_does_not_publish_output_when_ffprobe_validation_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    destination = tmp_path / "final.mp4"
    source.write_bytes(b"source")
    destination.write_bytes(b"previous-valid-output")
    monkeypatch.setattr(rendering.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(rendering, "available_encoder", lambda *_args: "libx264")

    def fake_execute(command, duration, progress, control=None):
        Path(command[-1]).write_bytes(b"x" * 2048)

    monkeypatch.setattr(rendering, "_execute", fake_execute)

    try:
        rendering.render_clip(source, destination, 0, 10, "16:9")
    except rendering.RenderError as error:
        assert "FFprobe" in str(error)
    else:
        raise AssertionError("Missing FFprobe must prevent publishing an unverified output")

    assert destination.read_bytes() == b"previous-valid-output"
    assert not (tmp_path / "final.partial.mp4").exists()
