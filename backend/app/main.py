from __future__ import annotations

from contextlib import asynccontextmanager
import mimetypes
import json
import os
import uuid
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from .database import EXPORT_DIR, MEDIA_DIR, THUMBNAIL_DIR, ensure_storage, get_connection
from .media import create_thumbnail, probe_media
from .clip_boundaries import refine_candidate_boundaries
from .schemas import (
    DiscoveryCreate, DiscoveryResponse, MediaResponse, ProjectCreate, ProjectResponse,
    TranscriptionCreate, TranscriptionResponse, TranscriptionStatusResponse, ExportCreate, ExportResponse,
)
from . import ai_discovery, transcription, rendering

_EXPORT_WORKER_LOCK = threading.Lock()
_EXPORT_WORKER_WAKE = threading.Event()
_EXPORT_WORKER_THREAD: threading.Thread | None = None
_EXPORT_WORKER_STOP = threading.Event()

@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_storage()
    timestamp = now_iso()
    with get_connection() as connection:
        for table in ("transcription_jobs", "discovery_jobs"):
            connection.execute(
                f"""UPDATE {table} SET status='failed',
                    error='Proses terhenti karena aplikasi sebelumnya ditutup.',
                    updated_at=? WHERE status IN ('queued','processing')""",
                (timestamp,),
            )
        connection.execute(
            """UPDATE export_jobs SET status='failed',
               error='Rendering terputus karena aplikasi sebelumnya ditutup.',
               updated_at=? WHERE status='processing'""",
            (timestamp,),
        )
    _EXPORT_WORKER_STOP.clear()
    start_export_worker()
    yield
    _EXPORT_WORKER_STOP.set()
    _EXPORT_WORKER_WAKE.set()
    if _EXPORT_WORKER_THREAD is not None:
        _EXPORT_WORKER_THREAD.join(timeout=5)


app = FastAPI(
    title="Personal Clipper Studio API",
    version="0.2.0",
    description="Local-first API for projects, media import, transcription, AI clip discovery, and exports.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:1420", "http://localhost:1420", "http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpeg", ".mpg"}
MAX_UPLOAD_BYTES = int(os.environ.get("PCS_MAX_UPLOAD_BYTES", str(30 * 1024 * 1024 * 1024)))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_row(row, media_count: int = 0) -> ProjectResponse:
    return ProjectResponse(
        id=row["id"], name=row["name"], description=row["description"],
        created_at=row["created_at"], updated_at=row["updated_at"], media_count=media_count,
    )


def media_row(row) -> MediaResponse:
    return MediaResponse(
        id=row["id"], project_id=row["project_id"], original_name=row["original_name"],
        mime_type=row["mime_type"], file_size=row["file_size"], duration=row["duration"],
        width=row["width"], height=row["height"], fps=row["fps"],
        video_codec=row["video_codec"], audio_codec=row["audio_codec"],
        thumbnail_url=f"/api/media/{row['id']}/thumbnail" if row["thumbnail_name"] else None,
        preview_url=f"/api/media/{row['id']}/file", created_at=row["created_at"],
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "personal-clipper-local-api", "version": "0.2.0"}


@app.get("/api")
async def root() -> dict[str, str]:
    return {"name": "Personal Clipper Studio", "status": "projects-and-media"}


@app.get("/api/projects", response_model=list[ProjectResponse])
async def list_projects():
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT p.*, COUNT(m.id) AS media_count
               FROM projects p LEFT JOIN media_assets m ON m.project_id=p.id
               GROUP BY p.id ORDER BY p.updated_at DESC"""
        ).fetchall()
    return [project_row(row, row["media_count"]) for row in rows]


@app.post("/api/projects", response_model=ProjectResponse, status_code=201)
async def create_project(payload: ProjectCreate):
    project_id = str(uuid.uuid4())
    timestamp = now_iso()
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Nama proyek tidak boleh kosong.")
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO projects(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
            (project_id, name, payload.description.strip(), timestamp, timestamp),
        )
        row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return project_row(row)


@app.get("/api/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    with get_connection() as connection:
        row = connection.execute(
            """SELECT p.*, COUNT(m.id) AS media_count FROM projects p
               LEFT JOIN media_assets m ON m.project_id=p.id
               WHERE p.id=? GROUP BY p.id""", (project_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Proyek tidak ditemukan.")
    return project_row(row, row["media_count"])


@app.get("/api/projects/{project_id}/media", response_model=list[MediaResponse])
async def list_project_media(project_id: str):
    with get_connection() as connection:
        exists = connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Proyek tidak ditemukan.")
        rows = connection.execute(
            "SELECT * FROM media_assets WHERE project_id=? ORDER BY created_at DESC", (project_id,)
        ).fetchall()
    return [media_row(row) for row in rows]


@app.post("/api/projects/{project_id}/media", response_model=MediaResponse, status_code=201)
async def import_media(project_id: str, file: UploadFile = File(...)):
    original_name = Path(file.filename or "video").name
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Format video belum didukung.")
    with get_connection() as connection:
        exists = connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="Proyek tidak ditemukan.")

    asset_id = str(uuid.uuid4())
    stored_name = f"{asset_id}{extension}"
    destination = MEDIA_DIR / stored_name
    size = 0
    try:
        with destination.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Ukuran video melewati batas impor.")
                target.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="File video kosong.")

        metadata = probe_media(destination)
        thumb_name = f"{asset_id}.jpg"
        thumb_path = THUMBNAIL_DIR / thumb_name
        has_thumbnail = create_thumbnail(destination, thumb_path)
        timestamp = now_iso()
        with get_connection() as connection:
            connection.execute(
                """INSERT INTO media_assets(
                    id,project_id,original_name,stored_name,mime_type,file_size,duration,
                    width,height,fps,video_codec,audio_codec,thumbnail_name,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    asset_id, project_id, original_name, stored_name,
                    file.content_type or mimetypes.guess_type(original_name)[0] or "video/mp4",
                    size, metadata.get("duration"), metadata.get("width"), metadata.get("height"),
                    metadata.get("fps"), metadata.get("video_codec"), metadata.get("audio_codec"),
                    thumb_name if has_thumbnail else None, timestamp,
                ),
            )
            connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (timestamp, project_id))
            row = connection.execute("SELECT * FROM media_assets WHERE id=?", (asset_id,)).fetchone()
        return media_row(row)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except (ValueError, OSError) as error:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        await file.close()


def media_file(asset_id: str, thumbnail: bool = False) -> tuple[Path, str]:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM media_assets WHERE id=?", (asset_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Media tidak ditemukan.")
    if thumbnail:
        if not row["thumbnail_name"]:
            raise HTTPException(status_code=404, detail="Thumbnail belum tersedia.")
        path = THUMBNAIL_DIR / row["thumbnail_name"]
        media_type = "image/jpeg"
    else:
        path = MEDIA_DIR / row["stored_name"]
        media_type = row["mime_type"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File media tidak ditemukan di penyimpanan.")
    return path, media_type


@app.get("/api/media/{asset_id}/file")
async def get_media_file(asset_id: str):
    path, media_type = media_file(asset_id)
    return FileResponse(path, media_type=media_type)


@app.get("/api/media/{asset_id}/thumbnail")
async def get_media_thumbnail(asset_id: str):
    path, media_type = media_file(asset_id, thumbnail=True)
    return FileResponse(path, media_type=media_type)


@app.delete("/api/media/{asset_id}", status_code=204)
async def delete_media(asset_id: str):
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM media_assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Media tidak ditemukan.")
        active_export = connection.execute(
            "SELECT 1 FROM export_jobs WHERE media_id=? AND status IN ('queued','processing') LIMIT 1",
            (asset_id,),
        ).fetchone()
        if active_export:
            raise HTTPException(status_code=409, detail="Video tidak dapat dihapus saat export masih berjalan.")
        exports = connection.execute(
            "SELECT output_name FROM export_jobs WHERE media_id=? AND status='completed'",
            (asset_id,),
        ).fetchall()
        connection.execute("DELETE FROM media_assets WHERE id=?", (asset_id,))
    for export in exports:
        (EXPORT_DIR / export["output_name"]).unlink(missing_ok=True)
    (MEDIA_DIR / row["stored_name"]).unlink(missing_ok=True)
    if row["thumbnail_name"]:
        (THUMBNAIL_DIR / row["thumbnail_name"]).unlink(missing_ok=True)
    return Response(status_code=204)



def transcription_payload(job_id: str) -> dict:
    with get_connection() as connection:
        job = connection.execute(
            "SELECT * FROM transcription_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="Transkripsi tidak ditemukan.")
        segments = connection.execute(
            "SELECT start,end,text FROM transcript_segments WHERE job_id=? ORDER BY start",
            (job_id,),
        ).fetchall()
    return {
        **dict(job),
        "segments": [dict(segment) for segment in segments],
    }


def run_transcription_job(job_id: str, source_path: str, model_name: str, language: str | None) -> None:
    def update_progress(value: float) -> None:
        with get_connection() as connection:
            connection.execute(
                "UPDATE transcription_jobs SET progress=?, updated_at=? WHERE id=?",
                (value, now_iso(), job_id),
            )

    try:
        with get_connection() as connection:
            connection.execute(
                "UPDATE transcription_jobs SET status='processing', updated_at=? WHERE id=?",
                (now_iso(), job_id),
            )
        result = transcription.transcribe_file(
            Path(source_path), model_name=model_name, language=language, progress=update_progress
        )
        with get_connection() as connection:
            connection.executemany(
                "INSERT INTO transcript_segments(job_id,start,end,text) VALUES(?,?,?,?)",
                [
                    (job_id, item["start"], item["end"], item["text"])
                    for item in result["segments"]
                ],
            )
            connection.execute(
                """UPDATE transcription_jobs
                   SET status='completed', progress=1, detected_language=?,
                       language_probability=?, duration=?, updated_at=?
                   WHERE id=?""",
                (
                    result["language"], result["language_probability"],
                    result["duration"], now_iso(), job_id,
                ),
            )
    except Exception as error:
        with get_connection() as connection:
            connection.execute(
                "UPDATE transcription_jobs SET status='failed', error=?, updated_at=? WHERE id=?",
                (str(error)[:2000], now_iso(), job_id),
            )


@app.post(
    "/api/media/{asset_id}/transcription",
    response_model=TranscriptionResponse,
    status_code=202,
)
async def start_transcription(
    asset_id: str,
    background_tasks: BackgroundTasks,
    payload: TranscriptionCreate,
):
    if not transcription.is_available():
        raise HTTPException(
            status_code=503,
            detail="Whisper belum terpasang. Install dengan: pip install -e \"backend[transcription]\"",
        )
    source_path, _ = media_file(asset_id)
    job_id = str(uuid.uuid4())
    timestamp = now_iso()
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO transcription_jobs(
                id,media_id,status,model_name,language,created_at,updated_at
            ) VALUES(?,?,'queued',?,?,?,?)""",
            (job_id, asset_id, payload.model_name, payload.language, timestamp, timestamp),
        )
    background_tasks.add_task(
        run_transcription_job, job_id, str(source_path), payload.model_name, payload.language
    )
    return transcription_payload(job_id)


@app.get("/api/transcriptions/{job_id}", response_model=TranscriptionResponse)
async def get_transcription(job_id: str):
    return transcription_payload(job_id)



def discovery_payload(job_id: str) -> dict:
    with get_connection() as connection:
        job = connection.execute(
            "SELECT * FROM discovery_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="Analisis AI tidak ditemukan.")
        candidates = connection.execute(
            "SELECT * FROM clip_candidates WHERE discovery_job_id=? ORDER BY score DESC, start_seconds",
            (job_id,),
        ).fetchall()
    return {
        **dict(job),
        "candidates": [dict(candidate) for candidate in candidates],
    }


def normalize_candidate(
    raw: dict,
    chunk: list[dict],
    min_duration: int,
    max_duration: int,
) -> dict | None:
    import math

    try:
        target_start = float(raw["start_seconds"])
        target_end = float(raw["end_seconds"])
        score = int(raw["score"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(target_start) or not math.isfinite(target_end) or not 0 <= score <= 100:
        return None
    if not chunk:
        return None
    chunk_start = float(chunk[0]["start"])
    chunk_end = float(chunk[-1]["end"])
    if target_start < chunk_start - 1 or target_start > chunk_end:
        return None
    if target_end < chunk_start or target_end > chunk_end + 1:
        return None

    refined = refine_candidate_boundaries(
        target_start, target_end, chunk, min_duration, max_duration
    )
    if refined is None:
        return None
    start_seconds = float(refined["start_seconds"])
    end_seconds = float(refined["end_seconds"])

    def safe_text(field: str, limit: int) -> str:
        value = raw.get(field)
        return value.strip()[:limit] if isinstance(value, str) else ""

    title = safe_text("title", 120)
    hook = safe_text("hook", 240)
    summary = safe_text("summary", 1000)
    rationale = safe_text("rationale", 1000)
    category = safe_text("category", 40) or "other"
    if not title or not hook or not summary or not rationale:
        return None
    return {
        "start_seconds": round(start_seconds, 3),
        "end_seconds": round(end_seconds, 3),
        "title": title,
        "hook": hook,
        "summary": summary,
        "rationale": rationale,
        "score": score,
        "category": category,
        "boundary_quality": int(refined["boundary_quality"]),
        "opening_boundary": str(refined["opening_boundary"])[:160],
        "ending_boundary": str(refined["ending_boundary"])[:160],
        "context_note": str(refined["context_note"])[:320],
    }


def run_discovery_job(job_id: str) -> None:
    try:
        with get_connection() as connection:
            job = connection.execute(
                "SELECT * FROM discovery_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if job is None:
                return
            segments = connection.execute(
                """SELECT start,end,text FROM transcript_segments
                   WHERE job_id=?
                   ORDER BY start""",
                (job["transcription_id"],),
            ).fetchall()
        transcript = [dict(item) for item in segments]
        if not transcript:
            raise ValueError("Transkrip tidak memiliki segmen untuk dianalisis.")
        chunks = ai_discovery.split_transcript(transcript)
        if not chunks:
            raise ValueError("Transkrip kosong atau tidak dapat dibagi menjadi bagian analisis.")

        with get_connection() as connection:
            connection.execute(
                "UPDATE discovery_jobs SET status='processing', updated_at=? WHERE id=?",
                (now_iso(), job_id),
            )

        for chunk_index, chunk in enumerate(chunks):
            raw_candidates = ai_discovery.analyze_transcript_chunk(
                chunk,
                mode=job["mode"],
                category=job["category"],
                min_duration=job["min_duration"],
                max_duration=job["max_duration"],
            )
            accepted: list[dict] = []
            for raw in raw_candidates:
                if not isinstance(raw, dict):
                    continue
                candidate = normalize_candidate(
                    raw, chunk, job["min_duration"], job["max_duration"]
                )
                if candidate is None or candidate["score"] < 40:
                    continue
                duration = candidate["end_seconds"] - candidate["start_seconds"]
                duplicate = False
                for existing in accepted:
                    overlap = max(
                        0.0,
                        min(candidate["end_seconds"], existing["end_seconds"])
                        - max(candidate["start_seconds"], existing["start_seconds"]),
                    )
                    shorter = min(
                        duration, existing["end_seconds"] - existing["start_seconds"]
                    )
                    if shorter > 0 and overlap / shorter >= 0.7:
                        duplicate = True
                        break
                if duplicate:
                    continue
                accepted.append(candidate)

            with get_connection() as connection:
                existing_rows = connection.execute(
                    "SELECT start_seconds,end_seconds FROM clip_candidates WHERE discovery_job_id=?",
                    (job_id,),
                ).fetchall()
                existing_ranges = [
                    (float(row["start_seconds"]), float(row["end_seconds"]))
                    for row in existing_rows
                ]
                for candidate in accepted:
                    candidate_duration = candidate["end_seconds"] - candidate["start_seconds"]
                    is_duplicate = False
                    for existing_start, existing_end in existing_ranges:
                        overlap = max(
                            0.0,
                            min(candidate["end_seconds"], existing_end)
                            - max(candidate["start_seconds"], existing_start),
                        )
                        shorter = min(candidate_duration, existing_end - existing_start)
                        if shorter > 0 and overlap / shorter >= 0.7:
                            is_duplicate = True
                            break
                    if is_duplicate:
                        continue
                    connection.execute(
                        """INSERT OR IGNORE INTO clip_candidates(
                            id,discovery_job_id,start_seconds,end_seconds,title,hook,
                            summary,rationale,score,category,boundary_quality,
                            opening_boundary,ending_boundary,context_note,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            str(uuid.uuid4()), job_id, candidate["start_seconds"],
                            candidate["end_seconds"], candidate["title"], candidate["hook"],
                            candidate["summary"], candidate["rationale"], candidate["score"],
                            candidate["category"], candidate["boundary_quality"],
                            candidate["opening_boundary"], candidate["ending_boundary"],
                            candidate["context_note"], now_iso(),
                        ),
                    )
                progress = (chunk_index + 1) / len(chunks)
                connection.execute(
                    "UPDATE discovery_jobs SET progress=?, updated_at=? WHERE id=?",
                    (progress, now_iso(), job_id),
                )

        with get_connection() as connection:
            connection.execute(
                "UPDATE discovery_jobs SET status='completed', progress=1, updated_at=? WHERE id=?",
                (now_iso(), job_id),
            )
    except Exception as error:
        with get_connection() as connection:
            connection.execute(
                "UPDATE discovery_jobs SET status='failed', error=?, updated_at=? WHERE id=?",
                (str(error)[:2000], now_iso(), job_id),
            )


@app.post(
    "/api/media/{asset_id}/discovery",
    response_model=DiscoveryResponse,
    status_code=202,
)
async def start_discovery(
    asset_id: str,
    background_tasks: BackgroundTasks,
    payload: DiscoveryCreate,
):
    if payload.max_duration < payload.min_duration:
        raise HTTPException(
            status_code=422, detail="Durasi maksimal harus lebih besar atau sama dengan durasi minimal."
        )
    if payload.mode == "category" and not payload.category:
        raise HTTPException(
            status_code=422, detail="Pilih kategori untuk mode Category Analysis."
        )
    try:
        settings = ai_discovery.provider_settings()
    except ai_discovery.DiscoveryProviderError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    with get_connection() as connection:
        media = connection.execute(
            "SELECT id FROM media_assets WHERE id=?", (asset_id,)
        ).fetchone()
        if media is None:
            raise HTTPException(status_code=404, detail="Media tidak ditemukan.")
        transcript = connection.execute(
            """SELECT id,status FROM transcription_jobs
               WHERE id=? AND media_id=?""",
            (payload.transcription_id, asset_id),
        ).fetchone()
    if transcript is None:
        raise HTTPException(status_code=404, detail="Transkrip untuk video ini tidak ditemukan.")
    if transcript["status"] != "completed":
        raise HTTPException(
            status_code=409, detail="Transkripsi harus selesai sebelum analisis AI dimulai."
        )

    job_id = str(uuid.uuid4())
    timestamp = now_iso()
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO discovery_jobs(
                id,media_id,transcription_id,status,provider,model_name,mode,category,
                min_duration,max_duration,created_at,updated_at
            ) VALUES(?,?,?,'queued',?,?,?,?,?,?,?,?)""",
            (
                job_id, asset_id, payload.transcription_id, settings["provider"],
                settings["model"], payload.mode, payload.category,
                payload.min_duration, payload.max_duration, timestamp, timestamp,
            ),
        )
    background_tasks.add_task(run_discovery_job, job_id)
    return discovery_payload(job_id)


@app.get("/api/discovery/{job_id}", response_model=DiscoveryResponse)
async def get_discovery(job_id: str):
    return discovery_payload(job_id)



@app.get("/api/transcriptions/{job_id}/status", response_model=TranscriptionStatusResponse)
async def get_transcription_status(job_id: str):
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM transcription_jobs WHERE id=?", (job_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Transkripsi tidak ditemukan.")
    return dict(row)



@app.get(
    "/api/media/{asset_id}/transcriptions/latest",
    response_model=TranscriptionStatusResponse,
)
async def get_latest_transcription(asset_id: str):
    with get_connection() as connection:
        exists = connection.execute(
            "SELECT 1 FROM media_assets WHERE id=?", (asset_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Media tidak ditemukan.")
        row = connection.execute(
            """SELECT * FROM transcription_jobs
               WHERE media_id=? ORDER BY created_at DESC LIMIT 1""",
            (asset_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Belum ada transkripsi untuk video ini.")
    return dict(row)


@app.get(
    "/api/media/{asset_id}/discovery/latest",
    response_model=DiscoveryResponse,
)
async def get_latest_discovery(asset_id: str):
    with get_connection() as connection:
        exists = connection.execute(
            "SELECT 1 FROM media_assets WHERE id=?", (asset_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Media tidak ditemukan.")
        row = connection.execute(
            """SELECT id FROM discovery_jobs
               WHERE media_id=? ORDER BY created_at DESC LIMIT 1""",
            (asset_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Belum ada analisis AI untuk video ini.")
    return discovery_payload(row["id"])


def export_payload(job_id: str) -> dict:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM export_jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Export tidak ditemukan.")
    payload = dict(row)
    payload["clip_count"] = len(json.loads(payload["clip_segments"])) if payload.get("clip_segments") else 1
    settings = json.loads(payload["render_settings"]) if payload.get("render_settings") else {}
    payload.update(settings)
    payload["download_url"] = (
        f"/api/exports/{job_id}/file" if row["status"] == "completed" else None
    )
    return payload


def run_export_job(job_id: str, source_path: str, destination_path: str) -> None:
    control = rendering.register_control(job_id)

    def update_progress(value: float) -> None:
        with get_connection() as connection:
            connection.execute(
                "UPDATE export_jobs SET progress=?, updated_at=? WHERE id=? AND status='processing'",
                (max(0.0, min(0.99, value)), now_iso(), job_id),
            )

    try:
        with get_connection() as connection:
            job = connection.execute("SELECT * FROM export_jobs WHERE id=?", (job_id,)).fetchone()
            if job is None or job["status"] != "queued":
                return
            claimed = connection.execute(
                "UPDATE export_jobs SET status='processing', updated_at=? WHERE id=? AND status='queued'",
                (now_iso(), job_id),
            ).rowcount
            if not claimed:
                return
            job_data = dict(job)
        control.check()
        subtitle_payload = job_data.get("subtitle_payload")
        clip_segments = json.loads(job_data["clip_segments"]) if job_data.get("clip_segments") else [
            {"start": float(job_data["start_seconds"]), "end": float(job_data["end_seconds"])}
        ]
        result = rendering.render_sequence(
            Path(source_path), Path(destination_path), clip_segments,
            job_data["aspect_ratio"], progress=update_progress,
            subtitle_segments=json.loads(subtitle_payload) if subtitle_payload else None,
            subtitle_preset=job_data.get("subtitle_preset") or "clean",
            subtitle_style=json.loads(job_data["subtitle_style"]) if job_data.get("subtitle_style") else None,
            crop_zoom=float(job_data.get("crop_zoom") or 1.0),
            crop_x=float(job_data.get("crop_x") if job_data.get("crop_x") is not None else 0.5),
            crop_y=float(job_data.get("crop_y") if job_data.get("crop_y") is not None else 0.5),
            control=control,
            quality_profile=json.loads(job_data["render_settings"]).get("quality_profile", "balanced") if job_data.get("render_settings") else "balanced",
            frame_rate=json.loads(job_data["render_settings"]).get("frame_rate", 30) if job_data.get("render_settings") else 30,
            normalize_audio=json.loads(job_data["render_settings"]).get("normalize_audio", False) if job_data.get("render_settings") else False,
        )
        control.check()
        with get_connection() as connection:
            connection.execute(
                """UPDATE export_jobs SET status='completed', progress=1,
                   encoder=?, error=NULL, updated_at=? WHERE id=? AND status='processing'""",
                (result["encoder"], now_iso(), job_id),
            )
    except rendering.RenderCancelled:
        Path(destination_path).unlink(missing_ok=True)
        with get_connection() as connection:
            connection.execute(
                """UPDATE export_jobs SET status='cancelled', error='Export dibatalkan oleh pengguna.',
                   updated_at=? WHERE id=? AND status IN ('queued','processing')""",
                (now_iso(), job_id),
            )
    except Exception as error:
        Path(destination_path).unlink(missing_ok=True)
        with get_connection() as connection:
            connection.execute(
                """UPDATE export_jobs SET status='failed', error=?, updated_at=?
                   WHERE id=? AND status='processing'""",
                (str(error)[:3000], now_iso(), job_id),
            )
    finally:
        rendering.unregister_control(job_id)


def _export_worker_loop() -> None:
    while not _EXPORT_WORKER_STOP.is_set():
        with get_connection() as connection:
            job = connection.execute(
                """SELECT e.id, e.output_name, m.stored_name
                   FROM export_jobs e JOIN media_assets m ON m.id=e.media_id
                   WHERE e.status='queued' ORDER BY e.created_at ASC LIMIT 1"""
            ).fetchone()
        if job is None:
            _EXPORT_WORKER_WAKE.wait(timeout=1.0)
            _EXPORT_WORKER_WAKE.clear()
            continue
        run_export_job(job["id"], str(MEDIA_DIR / job["stored_name"]), str(EXPORT_DIR / job["output_name"]))


def start_export_worker() -> None:
    global _EXPORT_WORKER_THREAD
    with _EXPORT_WORKER_LOCK:
        if _EXPORT_WORKER_THREAD is not None and _EXPORT_WORKER_THREAD.is_alive():
            _EXPORT_WORKER_WAKE.set()
            return
        _EXPORT_WORKER_THREAD = threading.Thread(target=_export_worker_loop, name="pcs-export-worker", daemon=True)
        _EXPORT_WORKER_THREAD.start()
    _EXPORT_WORKER_WAKE.set()


def cancel_export_job(job_id: str) -> None:
    with get_connection() as connection:
        row = connection.execute("SELECT status FROM export_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Export tidak ditemukan.")
        if row["status"] in ("completed", "failed", "cancelled"):
            raise HTTPException(status_code=409, detail="Export sudah selesai dan tidak dapat dibatalkan.")
        if row["status"] == "queued":
            changed = connection.execute(
                """UPDATE export_jobs SET status='cancelled',
                   error='Export dibatalkan sebelum rendering dimulai.', updated_at=?
                   WHERE id=? AND status='queued'""",
                (now_iso(), job_id),
            ).rowcount
            if changed:
                return
    control = rendering.get_control(job_id)
    if control is None:
        with get_connection() as connection:
            connection.execute(
                """UPDATE export_jobs SET status='cancelled',
                   error='Export dibatalkan sebelum proses rendering aktif.', updated_at=?
                   WHERE id=? AND status='processing'""",
                (now_iso(), job_id),
            )
        return
    control.cancel()
    # The active worker persists the terminal state after renderer cleanup.
    deadline = time.monotonic() + 30
    while rendering.get_control(job_id) is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    with get_connection() as connection:
        current = connection.execute("SELECT status FROM export_jobs WHERE id=?", (job_id,)).fetchone()
        if current and current["status"] == "processing":
            connection.execute(
                """UPDATE export_jobs SET status='cancelled',
                   error='Export dibatalkan oleh pengguna.', updated_at=?
                   WHERE id=? AND status='processing'""",
                (now_iso(), job_id),
            )




@app.post("/api/media/{asset_id}/exports", response_model=ExportResponse, status_code=202)
async def start_export(
    asset_id: str,
    payload: ExportCreate,
):
    if payload.end_seconds <= payload.start_seconds:
        raise HTTPException(status_code=422, detail="Titik akhir harus lebih besar dari titik awal.")
    preset_ratios = {"youtube_shorts": "9:16", "instagram_reels": "9:16", "facebook_reels": "9:16", "square_feed": "1:1", "landscape": "16:9"}
    expected_ratio = preset_ratios.get(payload.output_preset)
    if expected_ratio and payload.aspect_ratio != expected_ratio:
        raise HTTPException(status_code=422, detail="Rasio aspek tidak sesuai dengan preset output yang dipilih.")
    clip_segments = [
        {
            "start": segment.start,
            "end": segment.end,
            "crop_zoom": payload.crop_zoom if segment.crop_zoom is None else segment.crop_zoom,
            "crop_x": payload.crop_x if segment.crop_x is None else segment.crop_x,
            "crop_y": payload.crop_y if segment.crop_y is None else segment.crop_y,
            "audio_volume": payload.audio_volume if segment.audio_volume is None else segment.audio_volume,
        }
        for segment in payload.clip_segments
    ] or [{
        "start": payload.start_seconds, "end": payload.end_seconds,
        "crop_zoom": payload.crop_zoom, "crop_x": payload.crop_x, "crop_y": payload.crop_y,
        "audio_volume": payload.audio_volume,
    }]
    for segment in clip_segments:
        if segment["end"] <= segment["start"]:
            raise HTTPException(status_code=422, detail="Titik akhir setiap klip harus lebih besar dari titik awal.")
    try:
        rendering.available_encoder()
        if payload.burn_subtitles and not rendering.subtitles_filter_available():
            raise rendering.RenderError("FFmpeg tidak memiliki filter subtitles/libass. Burn-in subtitle tidak dapat dijalankan.")
    except rendering.RenderError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if payload.burn_subtitles and not payload.subtitle_segments:
        raise HTTPException(status_code=422, detail="Tidak ada segmen subtitle untuk di-burn-in.")
    if payload.burn_subtitles:
        for segment in payload.subtitle_segments:
            if segment.end <= segment.start:
                raise HTTPException(status_code=422, detail="Rentang waktu subtitle tidak valid.")

    with get_connection() as connection:
        media = connection.execute(
            "SELECT * FROM media_assets WHERE id=?", (asset_id,)
        ).fetchone()
    if media is None:
        raise HTTPException(status_code=404, detail="Media tidak ditemukan.")
    if media["duration"] is not None and any(
        segment["end"] > float(media["duration"]) + 0.05 for segment in clip_segments
    ):
        raise HTTPException(status_code=422, detail="Salah satu klip melewati durasi video sumber.")
    if payload.burn_subtitles and media["duration"] is not None and any(
        segment.end > float(media["duration"]) + 0.05 for segment in payload.subtitle_segments
    ):
        raise HTTPException(status_code=422, detail="Timestamp subtitle melewati durasi video sumber.")
    source_path = MEDIA_DIR / media["stored_name"]
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="File video sumber tidak ditemukan.")
    job_id = str(uuid.uuid4())
    output_name = f"clip-{job_id}.mp4"
    destination = EXPORT_DIR / output_name
    timestamp = now_iso()
    subtitle_payload = json.dumps(
        [segment.model_dump() for segment in payload.subtitle_segments],
        ensure_ascii=False,
    ) if payload.burn_subtitles else None
    subtitle_style_payload = json.dumps(payload.subtitle_style.model_dump()) if payload.burn_subtitles else None
    clip_payload = json.dumps(clip_segments)
    render_settings_payload = json.dumps({
        "output_preset": payload.output_preset,
        "quality_profile": payload.quality_profile,
        "frame_rate": payload.frame_rate,
        "normalize_audio": payload.normalize_audio,
    })
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO export_jobs(
                id,media_id,status,start_seconds,end_seconds,aspect_ratio,output_name,
                subtitle_payload,subtitle_preset,subtitle_style,clip_segments,crop_zoom,crop_x,crop_y,render_settings,created_at,updated_at
             ) VALUES(?,?,'queued',?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id, asset_id,
                min(segment["start"] for segment in clip_segments),
                max(segment["end"] for segment in clip_segments),
                payload.aspect_ratio, output_name, subtitle_payload,
                payload.subtitle_preset, subtitle_style_payload, clip_payload, payload.crop_zoom, payload.crop_x, payload.crop_y, render_settings_payload, timestamp, timestamp,
            ),
        )
    _EXPORT_WORKER_WAKE.set()
    return export_payload(job_id)


@app.get("/api/exports", response_model=list[ExportResponse])
async def list_exports():
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id FROM export_jobs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    return [export_payload(row["id"]) for row in rows]


@app.get("/api/exports/{job_id}", response_model=ExportResponse)
async def get_export(job_id: str):
    return export_payload(job_id)


@app.post("/api/exports/{job_id}/cancel", response_model=ExportResponse)
async def cancel_export(job_id: str):
    cancel_export_job(job_id)
    return export_payload(job_id)


@app.get("/api/exports/{job_id}/file")
async def download_export(job_id: str):
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM export_jobs WHERE id=?", (job_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Export tidak ditemukan.")
    if row["status"] != "completed":
        raise HTTPException(status_code=409, detail="File export belum siap diunduh.")
    path = EXPORT_DIR / row["output_name"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File hasil export tidak ditemukan.")
    return FileResponse(path, media_type="video/mp4", filename=row["output_name"])
