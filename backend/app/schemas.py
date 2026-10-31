from typing import Literal\n\nfrom pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    created_at: str
    updated_at: str
    media_count: int = 0


class MediaResponse(BaseModel):
    id: str
    project_id: str
    original_name: str
    mime_type: str
    file_size: int
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    thumbnail_url: str | None = None
    preview_url: str
    created_at: str


class TranscriptionCreate(BaseModel):
    model_name: str = Field(default="small", pattern="^(tiny|base|small|medium|large-v3|distil-large-v3)$")
    language: str | None = Field(default=None, min_length=2, max_length=10)


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class TranscriptionResponse(BaseModel):
    id: str
    media_id: str
    status: str
    model_name: str
    language: str | None = None
    detected_language: str | None = None
    language_probability: float | None = None
    duration: float | None = None
    progress: float
    error: str | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
    created_at: str
    updated_at: str



class DiscoveryCreate(BaseModel):
    transcription_id: str
    mode: Literal["auto", "category"] = "auto"
    category: Literal[
        "podcast", "gaming", "education", "motivation",
        "comedy", "news", "storytelling", "custom",
    ] | None = None
    min_duration: int = Field(default=30, ge=15, le=180)
    max_duration: int = Field(default=60, ge=15, le=180)


class ClipCandidateResponse(BaseModel):
    id: str
    discovery_job_id: str
    start_seconds: float
    end_seconds: float
    title: str
    hook: str
    summary: str
    rationale: str
    score: int
    category: str
    created_at: str


class DiscoveryResponse(BaseModel):
    id: str
    media_id: str
    transcription_id: str
    status: str
    provider: str
    model_name: str
    mode: str
    category: str | None = None
    min_duration: int
    max_duration: int
    progress: float
    error: str | None = None
    candidates: list[ClipCandidateResponse] = Field(default_factory=list)
    created_at: str
    updated_at: str
