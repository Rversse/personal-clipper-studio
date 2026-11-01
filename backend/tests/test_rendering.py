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
    (database.MEDIA_DIR / "source.mp4").write_bytes(b"source-video")
    with database.get_connection() as connection:
        connection.execute(
            "INSERT INTO projects(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
            (project_id, "Export test", "", timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO media_assets(
                id,project_id,original_name,stored_name,mime_type,file_size,duration,created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (media_id, project_id, "source.mp4", "source.mp4", "video/mp4", 12, 120.0, timestamp),
        )
    return media_id


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
            job_id, str(database.MEDIA_DIR / "source.mp4"),
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
        calls.append((start, end, output_path))
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
    progress_values = []
    subtitle_style = {"font_size": 68, "vertical_position": 10, "outline_width": 3.5, "background_opacity": 35}
    result = rendering.render_sequence(
        source, destination,
        [{"start": 30, "end": 40}, {"start": 5, "end": 15}],
        "9:16", progress=progress_values.append, subtitle_style=subtitle_style,
    )
    assert [(item[0], item[1]) for item in calls] == [(30, 40), (5, 15)]
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
            {"start": 30.0, "end": 40.0},
            {"start": 5.0, "end": 15.0},
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
