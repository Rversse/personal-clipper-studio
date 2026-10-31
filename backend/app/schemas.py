from typing import Literal

from pydantic import BaseModel, Field


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


class TranscriptionStatusResponse(BaseModel):
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
    created_at: str
    updated_at: str

class ExportSubtitleSegment(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    text: str = Field(max_length=500)


class ExportClipSegment(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)


class ExportCreate(BaseModel):
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    burn_subtitles: bool = False
    subtitle_preset: Literal["clean", "bold", "highlight"] = "clean"
    subtitle_segments: list[ExportSubtitleSegment] = Field(default_factory=list, max_length=10000)
    clip_segments: list[ExportClipSegment] = Field(default_factory=list, max_length=100)
    crop_zoom: float = Field(default=1.0, ge=1.0, le=3.0, allow_inf_nan=False)
    crop_x: float = Field(default=0.5, ge=0.0, le=1.0, allow_inf_nan=False)
    crop_y: float = Field(default=0.5, ge=0.0, le=1.0, allow_inf_nan=False)


class ExportResponse(BaseModel):
    id: str
    media_id: str
    status: str
    start_seconds: float
    end_seconds: float
    aspect_ratio: str
    output_name: str
    clip_count: int = 1
    encoder: str | None = None
    progress: float
    error: str | None = None
    download_url: str | None = None
    created_at: str
    updated_at: str
