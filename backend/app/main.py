from __future__ import annotations

from contextlib import asynccontextmanager
import mimetypes
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from .database import MEDIA_DIR, THUMBNAIL_DIR, ensure_storage, get_connection
from .media import create_thumbnail, probe_media
from .schemas import (MediaResponse, ProjectCreate, ProjectResponse, TranscriptionCreate, TranscriptionResponse)
from . import transcription

@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_storage()
    yield


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
        connection.execute("DELETE FROM media_assets WHERE id=?", (asset_id,))
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
