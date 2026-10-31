from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Personal Clipper Studio API",
    version="0.1.0",
    description="Local-first API for projects, transcription, AI clip discovery, and exports.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:1420", "http://localhost:1420", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "personal-clipper-local-api", "version": "0.1.0"}


@app.get("/api")
async def root() -> dict[str, str]:
    return {"name": "Personal Clipper Studio", "status": "foundation"}
