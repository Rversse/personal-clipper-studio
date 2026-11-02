from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
import hmac
import secrets
import re
import unicodedata
import uuid
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from .database import EXPORT_DIR, MEDIA_DIR, THUMBNAIL_DIR, ensure_storage, get_connection
from .media import create_thumbnail, probe_media
from .clip_boundaries import refine_candidate_boundaries
from .schemas import (
    DiscoveryCreate, DiscoveryResponse, MediaResponse, ProjectCreate, ProjectResponse,
    TranscriptionCreate, TranscriptionResponse, TranscriptionStatusResponse, TranscriptWordUpdate,
    ExportCreate, ExportResponse, ExportSubtitleSegment,
)
from . import ai_discovery, transcription, rendering, audio_analysis
from .diagnostics import router as diagnostics_router
from .security import ALLOWED_API_HOSTS, ALLOWED_UI_ORIGINS
from .ai_settings import router as ai_settings_router

_EXPORT_WORKER_LOCK = threading.Lock()
_EXPORT_WORKER_WAKE = threading.Event()
_EXPORT_WORKER_THREAD: threading.Thread | None = None
_EXPORT_WORKER_STOP = threading.Event()

_JOB_CONTROLS_LOCK = threading.Lock()
_JOB_CANCEL_EVENTS: dict[str, threading.Event] = {}
_JOB_SHUTDOWN = threading.Event()
_TRANSCRIPTION_WAKE = threading.Event()
_DISCOVERY_WAKE = threading.Event()
_JOB_WORKER_STOP = threading.Event()
_TRANSCRIPTION_WORKER_THREAD: threading.Thread | None = None
_DISCOVERY_WORKER_THREAD: threading.Thread | None = None
MAX_JOB_ATTEMPTS = 3

# A session token is injected only by the packaged Tauri host. Running the API
# manually for local development remains possible without desktop IPC.
_API_SESSION_TOKEN: str | None = None
_RESOURCE_TICKETS: dict[str, tuple[str, float]] = {}
_RESOURCE_TICKETS_LOCK = threading.Lock()
_RESOURCE_TICKET_TTL_SECONDS = 3600

def configure_session_token(token: str) -> None:
    """Bind the API to the one-time token received from the Tauri parent process."""
    global _API_SESSION_TOKEN
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", token):
        raise ValueError("Invalid desktop session token.")
    _API_SESSION_TOKEN = token




def issue_resource_ticket(resource_path: str) -> str:
    """Issue a short-lived, path-scoped capability for native media elements."""
    ticket = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _RESOURCE_TICKETS_LOCK:
        expired = [key for key, (_, deadline) in _RESOURCE_TICKETS.items() if deadline <= now]
        for key in expired:
            _RESOURCE_TICKETS.pop(key, None)
        if len(_RESOURCE_TICKETS) >= 4096:
            oldest = min(_RESOURCE_TICKETS, key=lambda key: _RESOURCE_TICKETS[key][1])
            _RESOURCE_TICKETS.pop(oldest, None)
        _RESOURCE_TICKETS[ticket] = (resource_path, now + _RESOURCE_TICKET_TTL_SECONDS)
    return ticket


def valid_resource_ticket(resource_path: str, ticket: str | None) -> bool:
    if not ticket:
        return False
    now = time.monotonic()
    with _RESOURCE_TICKETS_LOCK:
        entry = _RESOURCE_TICKETS.get(ticket)
        if entry is None:
            return False
        expected_path, deadline = entry
        if deadline <= now:
            _RESOURCE_TICKETS.pop(ticket, None)
            return False
        return hmac.compare_digest(expected_path, resource_path)


class JobCancelled(RuntimeError):
    """Raised when a transcription or discovery job is cancelled."""


def _register_job_control(job_id: str) -> threading.Event:
    event = threading.Event()
    with _JOB_CONTROLS_LOCK:
        _JOB_CANCEL_EVENTS[job_id] = event
        if _JOB_SHUTDOWN.is_set():
            event.set()
    return event


def _unregister_job_control(job_id: str) -> None:
    with _JOB_CONTROLS_LOCK:
        _JOB_CANCEL_EVENTS.pop(job_id, None)


def _recover_interrupted_jobs() -> None:
    """Requeue interrupted work after a crash, with a bounded crash-recovery budget."""
    timestamp = now_iso()
    with get_connection() as connection:
        for table in ("transcription_jobs", "discovery_jobs"):
            connection.execute(
                f"""UPDATE {table}
                    SET status=CASE WHEN attempt_count < ? THEN 'queued' ELSE 'failed' END,
                        progress=CASE WHEN attempt_count < ? THEN 0 ELSE progress END,
                        error=CASE WHEN attempt_count < ?
                            THEN NULL ELSE 'Pekerjaan dihentikan setelah beberapa kali crash aplikasi.' END,
                        updated_at=?
                    WHERE status='processing'
                       OR (status='queued' AND attempt_count >= ?)""",
                (MAX_JOB_ATTEMPTS, MAX_JOB_ATTEMPTS, MAX_JOB_ATTEMPTS, timestamp, MAX_JOB_ATTEMPTS),
            )


def _queued_transcription() -> tuple[str, str, str, str | None] | None:
    with get_connection() as connection:
        row = connection.execute(
            """SELECT j.id,j.model_name,j.language,m.id AS media_id,m.stored_name
               FROM transcription_jobs j
               JOIN media_assets m ON m.id=j.media_id
               WHERE j.status='queued' AND j.attempt_count < ?
               ORDER BY j.created_at,j.id LIMIT 1""",
            (MAX_JOB_ATTEMPTS,),
        ).fetchone()
    if row is None:
        return None
    extension = Path(str(row["stored_name"])).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        with get_connection() as connection:
            connection.execute(
                "UPDATE transcription_jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='queued'",
                ("Referensi file media tidak valid; transkripsi tidak dapat dijalankan.", now_iso(), row["id"]),
            )
        return ("", "", "", None)
    source = _managed_file_path(MEDIA_DIR, row["stored_name"], f"{row['media_id']}{extension}")
    if not source.is_file():
        with get_connection() as connection:
            connection.execute(
                "UPDATE transcription_jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='queued'",
                ("File media sumber tidak ditemukan; transkripsi tidak dapat dijalankan.", now_iso(), row["id"]),
            )
        return ("", "", "", None)
    return (str(row["id"]), str(source), str(row["model_name"]), row["language"])


def _job_worker_loop(job_type: str) -> None:
    wake = _TRANSCRIPTION_WAKE if job_type == "transcription" else _DISCOVERY_WAKE
    while not _JOB_WORKER_STOP.is_set():
        wake.clear()
        if _JOB_WORKER_STOP.is_set():
            break
        if job_type == "transcription":
            queued = _queued_transcription()
            if queued is not None:
                job_id, source_path, model_name, language = queued
                if job_id:
                    run_transcription_job(job_id, source_path, model_name, language)
                continue
        else:
            with get_connection() as connection:
                row = connection.execute(
                    """SELECT id FROM discovery_jobs
                       WHERE status='queued' AND attempt_count < ?
                       ORDER BY created_at,id LIMIT 1""",
                    (MAX_JOB_ATTEMPTS,),
                ).fetchone()
            if row is not None:
                run_discovery_job(str(row["id"]))
                continue
        wake.wait(0.75)


def start_job_workers() -> None:
    global _TRANSCRIPTION_WORKER_THREAD, _DISCOVERY_WORKER_THREAD
    _JOB_WORKER_STOP.clear()
    for name, job_type, current in (
        ("pcs-transcription-worker", "transcription", _TRANSCRIPTION_WORKER_THREAD),
        ("pcs-discovery-worker", "discovery", _DISCOVERY_WORKER_THREAD),
    ):
        if current is not None and current.is_alive():
            continue
        worker = threading.Thread(target=_job_worker_loop, args=(job_type,), name=name, daemon=True)
        worker.start()
        if job_type == "transcription":
            _TRANSCRIPTION_WORKER_THREAD = worker
        else:
            _DISCOVERY_WORKER_THREAD = worker
    _TRANSCRIPTION_WAKE.set()
    _DISCOVERY_WAKE.set()


def stop_job_workers() -> None:
    """Cooperatively stop active work and leave unfinished jobs durable for next launch."""
    _JOB_SHUTDOWN.set()
    with _JOB_CONTROLS_LOCK:
        active = list(_JOB_CANCEL_EVENTS.values())
    for event in active:
        event.set()
    _JOB_WORKER_STOP.set()
    _TRANSCRIPTION_WAKE.set()
    _DISCOVERY_WAKE.set()
    for worker in (_TRANSCRIPTION_WORKER_THREAD, _DISCOVERY_WORKER_THREAD):
        if worker is not None:
            worker.join(timeout=10)
    with get_connection() as connection:
        for table in ("transcription_jobs", "discovery_jobs"):
            connection.execute(
                f"UPDATE {table} SET status='queued',progress=0,error=NULL,updated_at=? WHERE status='processing'",
                (now_iso(),),
            )

def _is_completed_export(path: Path) -> bool:
    """Only treat an interrupted export as complete when FFprobe can read its MP4."""
    try:
        rendering.validate_mp4_output(path)
        return True
    except (rendering.RenderError, OSError):
        return False


def _cleanup_interrupted_export_artifacts(destination: Path) -> None:
    """Remove only temporary files owned by this export's deterministic filename."""
    partial = destination.with_name(destination.stem + ".partial" + destination.suffix)
    partial.unlink(missing_ok=True)
    for artifact in destination.parent.glob(f".{destination.stem}-*"):
        if artifact.is_file():
            artifact.unlink(missing_ok=True)


def recover_interrupted_exports() -> None:
    """Resume interrupted renders, preserving outputs atomically committed before a crash."""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id, output_name FROM export_jobs WHERE status='processing'"
        ).fetchall()

    for row in rows:
        output_name = str(row["output_name"])
        try:
            destination = _managed_file_path(EXPORT_DIR, output_name, f"clip-{row['id']}.mp4")
        except HTTPException:
            with get_connection() as connection:
                connection.execute(
                    """UPDATE export_jobs SET status='failed',
                       error='Referensi file output tidak valid; export tidak dapat dipulihkan.',
                       updated_at=? WHERE id=? AND status='processing'""",
                    (now_iso(), row["id"]),
                )
            continue

        if _is_completed_export(destination):
            # render_sequence uses os.replace only after FFmpeg produced a complete MP4.
            with get_connection() as connection:
                connection.execute(
                    """UPDATE export_jobs SET status='completed', progress=1,
                       error=NULL, updated_at=? WHERE id=? AND status='processing'""",
                    (now_iso(), row["id"]),
                )
            continue

        _cleanup_interrupted_export_artifacts(destination)
        destination.unlink(missing_ok=True)
        with get_connection() as connection:
            connection.execute(
                """UPDATE export_jobs SET status='queued', progress=0, encoder=NULL,
                   error=NULL, updated_at=? WHERE id=? AND status='processing'""",
                (now_iso(), row["id"]),
            )


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
    recover_interrupted_exports()
    _EXPORT_WORKER_STOP.clear()
    _JOB_SHUTDOWN.clear()
    start_export_worker()
    yield
    _EXPORT_WORKER_STOP.set()
    _JOB_SHUTDOWN.set()
    _cancel_background_jobs()
    _EXPORT_WORKER_WAKE.set()
    # Stop FFmpeg/FFprobe work before waiting for the worker to exit. This keeps
    # the desktop shutdown path from leaving orphaned encoder processes behind.
    rendering.cancel_all_controls()
    if _EXPORT_WORKER_THREAD is not None:
        _EXPORT_WORKER_THREAD.join(timeout=10)


app = FastAPI(
    title="Personal Clipper Studio API",
    version="0.2.0",
    description="Local-first API for projects, media import, transcription, AI clip discovery, and exports.",
    lifespan=lifespan,
)

app.include_router(diagnostics_router)
app.include_router(ai_settings_router)

@app.middleware("http")
async def enforce_trusted_browser_origin(request, call_next):
    """Enforce browser-origin, local-host, and desktop-session boundaries.

    The packaged desktop host configures a per-launch token. Development servers
    started independently remain usable without the Tauri session channel.
    """
    origin = request.headers.get("origin")
    if origin is not None:
        if origin not in ALLOWED_UI_ORIGINS:
            return JSONResponse(status_code=403, content={"detail": "Origin browser tidak diizinkan."})
        if request.url.hostname not in ALLOWED_API_HOSTS:
            return JSONResponse(status_code=421, content={"detail": "Host API lokal tidak diizinkan."})

    if _API_SESSION_TOKEN and request.url.path.startswith("/api/") and request.url.path != "/api/health":
        resource_path = request.url.path
        is_ticketed_resource = bool(
            re.fullmatch(r"/api/media/[0-9a-fA-F-]+/(?:file|thumbnail)", resource_path)
            or re.fullmatch(r"/api/exports/[0-9a-fA-F-]+/file", resource_path)
        )
        if is_ticketed_resource:
            if not valid_resource_ticket(resource_path, request.query_params.get("ticket")):
                return JSONResponse(status_code=401, content={"detail": "Akses file lokal tidak sah atau kedaluwarsa."})
        else:
            authorization = request.headers.get("authorization", "")
            scheme, _, supplied_token = authorization.partition(" ")
            if scheme.casefold() != "bearer" or not hmac.compare_digest(supplied_token, _API_SESSION_TOKEN):
                return JSONResponse(status_code=401, content={"detail": "Sesi aplikasi tidak valid."})

    response = await call_next(request)
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ALLOWED_UI_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpeg", ".mpg"}
VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".m4v": "video/mp4",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
}
SAFE_FILE_RESPONSE_HEADERS = {"X-Content-Type-Options": "nosniff"}

def _managed_file_path(directory: Path, stored_name: str, expected_name: str) -> Path:
    """Resolve an app-owned filename without allowing DB values to escape storage."""
    if (
        not stored_name
        or stored_name != expected_name
        or Path(stored_name).name != stored_name
        or "/" in stored_name
        or "\\" in stored_name
    ):
        raise HTTPException(status_code=500, detail="Referensi file penyimpanan tidak valid.")
    root = directory.resolve()
    candidate = root / stored_name
    try:
        if candidate.is_symlink() or candidate.resolve(strict=False).parent != root:
            raise HTTPException(status_code=500, detail="Referensi file penyimpanan tidak valid.")
    except OSError as error:
        raise HTTPException(status_code=500, detail="Referensi file penyimpanan tidak dapat diverifikasi.") from error
    return candidate

MAX_UPLOAD_BYTES = int(os.environ.get("PCS_MAX_UPLOAD_BYTES", str(30 * 1024 * 1024 * 1024)))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_row(row, media_count: int = 0) -> ProjectResponse:
    return ProjectResponse(
        id=row["id"], name=row["name"], description=row["description"],
        created_at=row["created_at"], updated_at=row["updated_at"], media_count=media_count,
    )


def media_row(row) -> MediaResponse:
    preview_path = f"/api/media/{row['id']}/file"
    preview_ticket = issue_resource_ticket(preview_path) if _API_SESSION_TOKEN else None
    thumbnail_path = f"/api/media/{row['id']}/thumbnail"
    thumbnail_ticket = issue_resource_ticket(thumbnail_path) if _API_SESSION_TOKEN and row["thumbnail_name"] else None
    return MediaResponse(
        id=row["id"], project_id=row["project_id"], original_name=row["original_name"],
        mime_type=row["mime_type"], file_size=row["file_size"], duration=row["duration"],
        width=row["width"], height=row["height"], fps=row["fps"],
        video_codec=row["video_codec"], audio_codec=row["audio_codec"],
        thumbnail_url=(f"{thumbnail_path}?ticket={thumbnail_ticket}" if thumbnail_ticket else thumbnail_path) if row["thumbnail_name"] else None,
        preview_url=f"{preview_path}?ticket={preview_ticket}" if preview_ticket else preview_path,
        created_at=row["created_at"],
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
    # Keep incomplete uploads out of the namespace used by persisted media rows.
    staged_source = MEDIA_DIR / f".{asset_id}.uploading"
    thumb_name = f"{asset_id}.jpg"
    thumb_path = THUMBNAIL_DIR / thumb_name
    staged_thumbnail = THUMBNAIL_DIR / f".{asset_id}.uploading.jpg"
    size = 0
    try:
        with staged_source.open("xb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Ukuran video melewati batas impor.")
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if size == 0:
            raise HTTPException(status_code=400, detail="File video kosong.")

        metadata = probe_media(staged_source)
        has_thumbnail = create_thumbnail(staged_source, staged_thumbnail)
        if not has_thumbnail:
            staged_thumbnail.unlink(missing_ok=True)

        # Publish validated files atomically before committing their metadata.
        # If the database write fails, the exception handler removes both files.
        os.replace(staged_source, destination)
        if has_thumbnail:
            os.replace(staged_thumbnail, thumb_path)

        timestamp = now_iso()
        with get_connection() as connection:
            connection.execute(
                """INSERT INTO media_assets(
                    id,project_id,original_name,stored_name,mime_type,file_size,duration,
                    width,height,fps,video_codec,audio_codec,thumbnail_name,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    asset_id, project_id, original_name, stored_name,
                    VIDEO_MEDIA_TYPES[extension],
                    size, metadata.get("duration"), metadata.get("width"), metadata.get("height"),
                    metadata.get("fps"), metadata.get("video_codec"), metadata.get("audio_codec"),
                    thumb_name if has_thumbnail else None, timestamp,
                ),
            )
            connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (timestamp, project_id))
            row = connection.execute("SELECT * FROM media_assets WHERE id=?", (asset_id,)).fetchone()
            response_payload = media_row(row)
        return response_payload
    except HTTPException:
        staged_source.unlink(missing_ok=True)
        staged_thumbnail.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        thumb_path.unlink(missing_ok=True)
        raise
    except (ValueError, OSError) as error:
        staged_source.unlink(missing_ok=True)
        staged_thumbnail.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        thumb_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        staged_source.unlink(missing_ok=True)
        staged_thumbnail.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        thumb_path.unlink(missing_ok=True)
        raise
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
        path = _managed_file_path(THUMBNAIL_DIR, row["thumbnail_name"], f"{asset_id}.jpg")
        media_type = "image/jpeg"
    else:
        extension = Path(str(row["stored_name"])).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=415, detail="Tipe media tersimpan tidak didukung.")
        path = _managed_file_path(MEDIA_DIR, row["stored_name"], f"{asset_id}{extension}")
        # Do not trust MIME metadata persisted by older app versions; it may
        # have originated from the untrusted multipart Content-Type header.
        media_type = VIDEO_MEDIA_TYPES[extension]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File media tidak ditemukan di penyimpanan.")
    return path, media_type


@app.get("/api/media/{asset_id}/file")
async def get_media_file(asset_id: str):
    path, media_type = media_file(asset_id)
    return FileResponse(path, media_type=media_type, headers=SAFE_FILE_RESPONSE_HEADERS)


@app.get("/api/media/{asset_id}/thumbnail")
async def get_media_thumbnail(asset_id: str):
    path, media_type = media_file(asset_id, thumbnail=True)
    return FileResponse(path, media_type=media_type, headers=SAFE_FILE_RESPONSE_HEADERS)


@app.delete("/api/media/{asset_id}", status_code=204)
async def delete_media(asset_id: str):
    with get_connection() as connection:
        # Reserve the SQLite writer before checking job state so a new background
        # job cannot be queued between this check and the media-row deletion.
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM media_assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Media tidak ditemukan.")
        active_jobs = connection.execute(
            """SELECT 1 FROM (
                   SELECT id FROM transcription_jobs
                   WHERE media_id=? AND status IN ('queued','processing')
                   UNION ALL
                   SELECT id FROM discovery_jobs
                   WHERE media_id=? AND status IN ('queued','processing')
                   UNION ALL
                   SELECT id FROM export_jobs
                   WHERE media_id=? AND status IN ('queued','processing')
               ) LIMIT 1""",
            (asset_id, asset_id, asset_id),
        ).fetchone()
        if active_jobs:
            raise HTTPException(
                status_code=409,
                detail="Video tidak dapat dihapus saat transkripsi, AI discovery, atau export masih berjalan.",
            )
        exports = connection.execute(
            "SELECT id, output_name FROM export_jobs WHERE media_id=? AND status='completed'",
            (asset_id,),
        ).fetchall()
        # Validate every persisted path before committing the destructive DB
        # operation. A malformed/corrupted row must never delete outside storage.
        media_extension = Path(str(row["stored_name"])).suffix.lower()
        if media_extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=500, detail="Referensi media tersimpan tidak valid.")
        media_path = _managed_file_path(MEDIA_DIR, row["stored_name"], f"{asset_id}{media_extension}")
        thumbnail_path = (
            _managed_file_path(THUMBNAIL_DIR, row["thumbnail_name"], f"{asset_id}.jpg")
            if row["thumbnail_name"] else None
        )
        export_paths = [
            _managed_file_path(EXPORT_DIR, export["output_name"], f"clip-{export['id']}.mp4")
            for export in exports
        ]
        connection.execute("DELETE FROM media_assets WHERE id=?", (asset_id,))
    for export_path in export_paths:
        export_path.unlink(missing_ok=True)
    media_path.unlink(missing_ok=True)
    if thumbnail_path is not None:
        thumbnail_path.unlink(missing_ok=True)
    return Response(status_code=204)



def word_timing_matches_segment_text(segment_text: str, words: list[dict]) -> bool:
    """Use word events only when their text reconstructs the complete segment."""
    token_pattern = re.compile(r"\w+|[^\w\s]", re.UNICODE)

    def tokens(value: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return token_pattern.findall(normalized)

    expected = tokens(segment_text)
    actual = tokens(" ".join(str(word.get("text", "")).strip() for word in words))
    return bool(expected) and expected == actual


def transcription_payload(job_id: str) -> dict:
    with get_connection() as connection:
        # Keep job, segments, and word rows on one consistent SQLite snapshot.
        connection.execute("BEGIN")
        job = connection.execute(
            "SELECT * FROM transcription_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="Transkripsi tidak ditemukan.")
        segments = connection.execute(
            "SELECT id,start,end,text FROM transcript_segments WHERE job_id=? ORDER BY start",
            (job_id,),
        ).fetchall()
        word_rows = connection.execute(
            """SELECT segment_id,start,end,text,probability FROM transcript_words
               WHERE job_id=? ORDER BY segment_id,word_index""",
            (job_id,),
        ).fetchall()

    words_by_segment: dict[int, list[dict]] = {}
    for word in word_rows:
        words_by_segment.setdefault(int(word["segment_id"]), []).append({
            "start": word["start"],
            "end": word["end"],
            "text": word["text"],
            "probability": word["probability"],
        })
    segment_payloads = [
        {**dict(segment), "words": words_by_segment.get(int(segment["id"]), [])}
        for segment in segments
    ]
    return {
        **dict(job),
        "segments": segment_payloads,
    }


def run_transcription_job(job_id: str, source_path: str, model_name: str, language: str | None) -> None:
    cancel_event = _register_job_control(job_id)

    def check_cancelled() -> None:
        if cancel_event.is_set():
            raise JobCancelled("Transkripsi dibatalkan.")

    def update_progress(value: float) -> None:
        check_cancelled()
        with get_connection() as connection:
            connection.execute(
                "UPDATE transcription_jobs SET progress=?, updated_at=? WHERE id=? AND status='processing'",
                (value, now_iso(), job_id),
            )

    try:
        check_cancelled()
        with get_connection() as connection:
            claimed = connection.execute(
                "UPDATE transcription_jobs SET status='processing', updated_at=? WHERE id=? AND status='queued'",
                (now_iso(), job_id),
            ).rowcount
        if not claimed:
            return
        result = transcription.transcribe_file(
            Path(source_path), model_name=model_name, language=language,
            progress=update_progress, cancel_check=check_cancelled,
        )
        check_cancelled()
        with get_connection() as connection:
            current = connection.execute(
                "SELECT status FROM transcription_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if current is None or current["status"] != "processing":
                raise JobCancelled("Transkripsi dibatalkan.")
            connection.execute("DELETE FROM transcript_segments WHERE job_id=?", (job_id,))
            for item in result["segments"]:
                cursor = connection.execute(
                    "INSERT INTO transcript_segments(job_id,start,end,text) VALUES(?,?,?,?)",
                    (job_id, item["start"], item["end"], item["text"]),
                )
                segment_id = cursor.lastrowid
                valid_words = []
                previous_word_end = float(item["start"])
                for word in item.get("words", []):
                    try:
                        start = float(word["start"])
                        end = float(word["end"])
                        word_text = str(word["text"]).strip()
                        raw_probability = word.get("probability")
                        probability = float(raw_probability) if raw_probability is not None else None
                        import math
                        if (
                            word_text and math.isfinite(start) and math.isfinite(end)
                            and item["start"] <= previous_word_end <= start < end <= item["end"]
                            and (probability is None or math.isfinite(probability) and 0 <= probability <= 1)
                        ):
                            valid_words.append((job_id, segment_id, len(valid_words), start, end, word_text, probability))
                            previous_word_end = end
                    except (KeyError, TypeError, ValueError, OverflowError):
                        continue
                connection.executemany(
                    "INSERT INTO transcript_words(job_id,segment_id,word_index,start,end,text,probability) VALUES(?,?,?,?,?,?,?)",
                    valid_words,
                )
            connection.execute(
                "UPDATE transcription_jobs SET status='completed', progress=1, detected_language=?, language_probability=?, duration=?, error=NULL, updated_at=? WHERE id=? AND status='processing'",
                (result["language"], result["language_probability"], result["duration"], now_iso(), job_id),
            )
    except JobCancelled as error:
        with get_connection() as connection:
            connection.execute(
                "UPDATE transcription_jobs SET status='cancelled', error=?, updated_at=? WHERE id=? AND status IN ('queued','processing')",
                (str(error), now_iso(), job_id),
            )
    except Exception as error:
        with get_connection() as connection:
            connection.execute(
                "UPDATE transcription_jobs SET status='failed', error=?, updated_at=? WHERE id=? AND status='processing'",
                (str(error)[:2000], now_iso(), job_id),
            )
    finally:
        _unregister_job_control(job_id)


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
    model_preflight = transcription.whisper_model_preflight(payload.model_name)
    if model_preflight["disk_space_known"] and not model_preflight["disk_space_ok"]:
        required_mb = (model_preflight["required_free_bytes"] + 1024**2 - 1) // 1024**2
        free_mb = model_preflight["free_bytes"] // 1024**2
        raise HTTPException(
            status_code=507,
            detail=(
                f"Ruang disk tidak cukup untuk model Whisper {payload.model_name}. "
                f"Perlu sekitar {required_mb} MB ruang kosong; tersedia {free_mb} MB. "
                "Kosongkan ruang disk atau pilih model yang lebih kecil."
            ),
        )
    job_id = str(uuid.uuid4())
    timestamp = now_iso()
    with get_connection() as connection:
        # Serialize with media deletion so a stale file lookup cannot queue an orphan job.
        connection.execute("BEGIN IMMEDIATE")
        media = connection.execute(
            "SELECT stored_name FROM media_assets WHERE id=?", (asset_id,)
        ).fetchone()
        if media is None:
            raise HTTPException(status_code=404, detail="Media tidak ditemukan.")
        extension = Path(str(media["stored_name"])).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=500, detail="Referensi media tersimpan tidak valid.")
        source_path = _managed_file_path(MEDIA_DIR, media["stored_name"], f"{asset_id}{extension}")
        if not source_path.is_file():
            raise HTTPException(status_code=404, detail="File media tidak ditemukan di penyimpanan.")
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


@app.post("/api/transcriptions/{job_id}/cancel", response_model=TranscriptionStatusResponse)
async def cancel_transcription(job_id: str):
    with _JOB_CONTROLS_LOCK:
        event = _JOB_CANCEL_EVENTS.get(job_id)
    if event:
        event.set()
    with get_connection() as connection:
        row = connection.execute(
            "UPDATE transcription_jobs SET status='cancelled', error='Transkripsi dibatalkan oleh pengguna.', updated_at=? WHERE id=? AND status IN ('queued','processing') RETURNING *",
            (now_iso(), job_id),
        ).fetchone()
    if row is None:
        with get_connection() as connection:
            exists = connection.execute("SELECT status FROM transcription_jobs WHERE id=?", (job_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Transkripsi tidak ditemukan.")
        raise HTTPException(status_code=409, detail="Transkripsi sudah selesai atau tidak dapat dibatalkan.")
    return dict(row)


@app.post("/api/discovery/{job_id}/cancel", response_model=DiscoveryResponse)
async def cancel_discovery(job_id: str):
    with _JOB_CONTROLS_LOCK:
        event = _JOB_CANCEL_EVENTS.get(job_id)
    if event:
        event.set()
    with get_connection() as connection:
        row = connection.execute(
            "UPDATE discovery_jobs SET status='cancelled', error='Analisis AI dibatalkan oleh pengguna.', updated_at=? WHERE id=? AND status IN ('queued','processing') RETURNING id",
            (now_iso(), job_id),
        ).fetchone()
    if row is None:
        with get_connection() as connection:
            exists = connection.execute("SELECT status FROM discovery_jobs WHERE id=?", (job_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Analisis AI tidak ditemukan.")
        raise HTTPException(status_code=409, detail="Analisis AI sudah selesai atau tidak dapat dibatalkan.")
    return discovery_payload(job_id)


@app.patch("/api/transcriptions/{job_id}/segments/{segment_id}/words/{word_index}")
async def update_transcript_word(
    job_id: str,
    segment_id: int,
    word_index: int,
    payload: TranscriptWordUpdate,
):
    import math

    if not math.isfinite(payload.start) or not math.isfinite(payload.end) or payload.end <= payload.start:
        raise HTTPException(status_code=422, detail="Rentang waktu kata tidak valid.")
    if not payload.text.strip():
        raise HTTPException(status_code=422, detail="Teks kata tidak boleh kosong.")
    with get_connection() as connection:
        # Serialize adjacent-word validation and update to prevent concurrent overlap.
        connection.execute("BEGIN IMMEDIATE")
        segment = connection.execute(
            "SELECT start,end FROM transcript_segments WHERE id=? AND job_id=?",
            (segment_id, job_id),
        ).fetchone()
        if segment is None:
            raise HTTPException(status_code=404, detail="Segmen transkrip tidak ditemukan.")
        word = connection.execute(
            """SELECT id FROM transcript_words
               WHERE job_id=? AND segment_id=? AND word_index=?""",
            (job_id, segment_id, word_index),
        ).fetchone()
        if word is None:
            raise HTTPException(status_code=404, detail="Timestamp kata tidak ditemukan.")
        previous = connection.execute(
            "SELECT start,end FROM transcript_words WHERE segment_id=? AND word_index=?",
            (segment_id, word_index - 1),
        ).fetchone() if word_index > 0 else None
        following = connection.execute(
            "SELECT start,end FROM transcript_words WHERE segment_id=? AND word_index=?",
            (segment_id, word_index + 1),
        ).fetchone()
        if payload.start < segment["start"] or payload.end > segment["end"]:
            raise HTTPException(status_code=422, detail="Timestamp kata harus berada di dalam rentang segmen.")
        if previous and payload.start < previous["end"]:
            raise HTTPException(status_code=422, detail="Timestamp kata bertabrakan dengan kata sebelumnya.")
        if following and payload.end > following["start"]:
            raise HTTPException(status_code=422, detail="Timestamp kata bertabrakan dengan kata berikutnya.")
        connection.execute(
            """UPDATE transcript_words SET start=?,end=?,text=?
               WHERE job_id=? AND segment_id=? AND word_index=?""",
            (payload.start, payload.end, payload.text.strip(), job_id, segment_id, word_index),
        )
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
    context_segments: list[dict] | None = None,
    audio_features: dict | None = None,
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
        target_start, target_end, context_segments or chunk, min_duration, max_duration,
        audio_features=audio_features,
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
    cancel_event = _register_job_control(job_id)

    def check_cancelled() -> None:
        if cancel_event.is_set():
            raise JobCancelled("Analisis AI dibatalkan.")

    try:
        check_cancelled()
        with get_connection() as connection:
            job = connection.execute(
                "SELECT * FROM discovery_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if job is None:
                return
            segments = connection.execute(
                """SELECT id,start,end,text FROM transcript_segments
                   WHERE job_id=?
                   ORDER BY start""",
                (job["transcription_id"],),
            ).fetchall()
            word_rows = connection.execute(
                """SELECT segment_id,start,end,text,probability FROM transcript_words
                   WHERE job_id=? ORDER BY segment_id,word_index""",
                (job["transcription_id"],),
            ).fetchall()
            media = connection.execute(
                "SELECT id,stored_name,duration FROM media_assets WHERE id=?",
                (job["media_id"],),
            ).fetchone()
        words_by_segment: dict[int, list[dict]] = {}
        for word in word_rows:
            words_by_segment.setdefault(int(word["segment_id"]), []).append({
                "start": word["start"],
                "end": word["end"],
                "text": word["text"],
                "probability": word["probability"],
            })
        transcript = [
            {**dict(item), "words": words_by_segment.get(int(item["id"]), [])}
            for item in segments
        ]
        if not transcript:
            raise ValueError("Transkrip tidak memiliki segmen untuk dianalisis.")

        with get_connection() as connection:
            claimed = connection.execute(
                "UPDATE discovery_jobs SET status='processing', updated_at=? WHERE id=? AND status='queued'",
                (now_iso(), job_id),
            ).rowcount
        if not claimed:
            return
        check_cancelled()

        audio_features = None
        if media is not None:
            try:
                extension = Path(str(media["stored_name"])).suffix.lower()
                if extension not in ALLOWED_EXTENSIONS:
                    raise ValueError("Referensi media tersimpan tidak valid.")
                source_path = _managed_file_path(MEDIA_DIR, media["stored_name"], f"{media['id']}{extension}")
                audio_features = audio_analysis.analyze_audio(
                    source_path, duration=media["duration"]
                )
            except Exception:
                # Prosody is supplemental; unavailable audio analysis must not block discovery.
                audio_features = None
        chunks = ai_discovery.split_transcript(transcript)
        if not chunks:
            raise ValueError("Transkrip kosong atau tidak dapat dibagi menjadi bagian analisis.")

        for chunk_index, chunk in enumerate(chunks):
            check_cancelled()
            raw_candidates = ai_discovery.analyze_transcript_chunk(
                chunk,
                mode=job["mode"],
                category=job["category"],
                min_duration=job["min_duration"],
                max_duration=job["max_duration"],
            )
            check_cancelled()
            accepted: list[dict] = []
            for raw in raw_candidates:
                if not isinstance(raw, dict):
                    continue
                candidate = normalize_candidate(
                    raw, chunk, job["min_duration"], job["max_duration"],
                    context_segments=transcript,
                    audio_features=audio_features,
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
                current = connection.execute(
                    "SELECT status FROM discovery_jobs WHERE id=?", (job_id,)
                ).fetchone()
                if current is None or current["status"] != "processing":
                    raise JobCancelled("Analisis AI dibatalkan.")
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

        check_cancelled()
        with get_connection() as connection:
            connection.execute(
                "UPDATE discovery_jobs SET status='completed', progress=1, error=NULL, updated_at=? WHERE id=? AND status='processing'",
                (now_iso(), job_id),
            )
    except JobCancelled as error:
        with get_connection() as connection:
            connection.execute(
                "UPDATE discovery_jobs SET status='cancelled', error=?, updated_at=? WHERE id=? AND status IN ('queued','processing')",
                (str(error), now_iso(), job_id),
            )
    except Exception as error:
        with get_connection() as connection:
            connection.execute(
                "UPDATE discovery_jobs SET status='failed', error=?, updated_at=? WHERE id=? AND status='processing'",
                (str(error)[:2000], now_iso(), job_id),
            )
    finally:
        _unregister_job_control(job_id)


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

    job_id = str(uuid.uuid4())
    timestamp = now_iso()
    with get_connection() as connection:
        # Claim the write transaction before validating references and inserting the job.
        connection.execute("BEGIN IMMEDIATE")
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
    download_path = f"/api/exports/{job_id}/file"
    download_ticket = issue_resource_ticket(download_path) if _API_SESSION_TOKEN and row["status"] == "completed" else None
    payload["download_url"] = (
        f"{download_path}?ticket={download_ticket}" if download_ticket else download_path if row["status"] == "completed" else None
    )
    return payload


def run_export_job(job_id: str, source_path: str, destination_path: str) -> None:
    control = rendering.register_control(job_id)
    # Close the race where shutdown begins after the worker dequeues a job but
    # before its render control is visible to cancel_all_controls().
    if _EXPORT_WORKER_STOP.is_set():
        control.cancel()

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
                """SELECT e.id, e.output_name, m.id AS media_id, m.stored_name
                   FROM export_jobs e JOIN media_assets m ON m.id=e.media_id
                   WHERE e.status='queued' ORDER BY e.created_at ASC LIMIT 1"""
            ).fetchone()
        if job is None:
            _EXPORT_WORKER_WAKE.wait(timeout=1.0)
            _EXPORT_WORKER_WAKE.clear()
            continue
        try:
            extension = Path(str(job["stored_name"])).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=500, detail="Referensi media tersimpan tidak valid.")
            source_path = _managed_file_path(MEDIA_DIR, job["stored_name"], f"{job['media_id']}{extension}")
            destination_path = _managed_file_path(EXPORT_DIR, job["output_name"], f"clip-{job['id']}.mp4")
            if not source_path.is_file():
                raise HTTPException(status_code=404, detail="File video sumber tidak ditemukan.")
        except HTTPException as error:
            with get_connection() as connection:
                connection.execute(
                    "UPDATE export_jobs SET status='failed', error=?, updated_at=? WHERE id=? AND status='queued'",
                    (error.detail, now_iso(), job["id"]),
                )
            continue
        run_export_job(job["id"], str(source_path), str(destination_path))


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
    if payload.burn_subtitles and not payload.subtitle_segments:
        with get_connection() as connection:
            transcript_job = connection.execute(
                """SELECT id FROM transcription_jobs
                   WHERE media_id=? AND status='completed'
                   ORDER BY created_at DESC LIMIT 1""",
                (asset_id,),
            ).fetchone()
            if transcript_job:
                subtitle_rows = connection.execute(
                    """SELECT s.id,s.start AS segment_start,s.end AS segment_end,s.text,
                              w.start,w.end,w.text AS word_text
                       FROM transcript_segments s
                       LEFT JOIN transcript_words w ON w.segment_id=s.id
                       WHERE s.job_id=? ORDER BY s.start,w.word_index""",
                    (transcript_job["id"],),
                ).fetchall()
                segments_by_id: dict[int, dict] = {}
                for row in subtitle_rows:
                    segment_id = int(row["id"])
                    segment = segments_by_id.setdefault(segment_id, {
                        "start": row["segment_start"],
                        "end": row["segment_end"],
                        "text": row["text"],
                        "words": [],
                    })
                    if row["word_text"] is not None:
                        segment["words"].append({
                            "start": row["start"],
                            "end": row["end"],
                            "text": row["word_text"],
                        })
                for segment in segments_by_id.values():
                    if segment["words"] and word_timing_matches_segment_text(segment["text"], segment["words"]):
                        payload.subtitle_segments.extend(
                            ExportSubtitleSegment(**word) for word in segment["words"]
                        )
                    else:
                        # Missing or malformed word timestamps must not silently
                        # remove text from the rendered subtitle.
                        payload.subtitle_segments.append(
                            ExportSubtitleSegment(
                                start=segment["start"],
                                end=segment["end"],
                                text=segment["text"],
                            )
                        )
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
    media_extension = Path(str(media["stored_name"])).suffix.lower()
    if media_extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=500, detail="Referensi media tersimpan tidak valid.")
    source_path = _managed_file_path(MEDIA_DIR, media["stored_name"], f"{asset_id}{media_extension}")
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
        # Serialize the final media check and queue insert with media deletion.
        connection.execute("BEGIN IMMEDIATE")
        current_media = connection.execute(
            "SELECT 1 FROM media_assets WHERE id=?", (asset_id,)
        ).fetchone()
        if current_media is None:
            raise HTTPException(status_code=404, detail="Media tidak ditemukan.")
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
    path = _managed_file_path(EXPORT_DIR, row["output_name"], f"clip-{job_id}.mp4")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File hasil export tidak ditemukan.")
    return FileResponse(path, media_type="video/mp4", filename=row["output_name"], headers=SAFE_FILE_RESPONSE_HEADERS)
