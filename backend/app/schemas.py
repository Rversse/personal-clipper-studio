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
