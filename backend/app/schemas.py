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
    segments: list[TranscriptSegment] = []
    created_at: str
    updated_at: str
