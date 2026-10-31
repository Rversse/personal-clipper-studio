# Local API service

## Requirements

- Python 3.11 or newer

## Run locally

From the repository root:

```bash
python -m venv backend/.venv
# Windows PowerShell:
backend\.venv\Scripts\Activate.ps1
# macOS/Linux:
# source backend/.venv/bin/activate
pip install -e ./backend
uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765 --reload
```

Health check: `http://127.0.0.1:8765/api/health`

The service intentionally binds to loopback only. Do not expose this local API to a public network.

## Media import notes

- The local workspace is stored in `~/.personal-clipper-studio` by default (on Windows, under the current user's home directory).
- Set `PCS_DATA_DIR` to choose another local storage location.
- Set `PCS_MAX_UPLOAD_BYTES` to change the upload limit; default is 30 GiB.
- Install FFmpeg and ensure both `ffmpeg` and `ffprobe` are on PATH for automatic metadata probing and thumbnail generation. The original video is retained even when those tools are unavailable; metadata and thumbnails may then be incomplete.
- Supported source extensions: MP4, MOV, MKV, WEBM, AVI, M4V, MPEG, MPG.


## Local transcription (Phase 3)

Install the optional Whisper engine inside the backend environment:

```bash
pip install -e "backend[transcription]"
```

The first transcription downloads the selected Whisper model into the local model cache. Available presets are `tiny`, `base`, `small` (default), `medium`, `large-v3`, and `distil-large-v3`. Model size affects download size, memory use, speed, and accuracy.

- Set `PCS_WHISPER_DEVICE=auto` (default) to let CTranslate2 choose the available device, or set `cpu` / `cuda` explicitly.
- Set `PCS_WHISPER_COMPUTE_TYPE=default` (default), `int8`, or a supported CUDA compute type such as `float16`.
- Set the source language with the transcription request (for example, `id` for Indonesian); leave it empty for language detection.
- Transcription runs locally. Job status and timestamped transcript segments are stored in SQLite.
- CUDA acceleration depends on a compatible NVIDIA driver and CUDA/cuDNN runtime for the installed CTranslate2 build. CPU remains the fallback.


## AI clip discovery

AI discovery analyzes saved transcript segments; it does not upload the source video. It supports two providers:

- **Gemini (default):** set `PCS_GEMINI_API_KEY`; optionally set `PCS_GEMINI_MODEL` (default `gemini-3.8-flash`). Requests use the Gemini Interactions API with structured JSON output and `store=false`.
- **Ollama:** install and start Ollama locally, pull a model that supports JSON-schema output, then set `PCS_AI_PROVIDER=ollama`; optionally set `PCS_OLLAMA_URL` (default `http://127.0.0.1:11434`) and `PCS_OLLAMA_MODEL` (default `qwen3:8b`).

Configuration is read from the backend process environment. API keys are never written to SQLite or returned by the API. Do not put secrets in frontend environment variables or commit them to the repository.

The discovery endpoint requires a completed transcription. It analyzes transcript chunks sequentially with overlap, validates returned timestamps against real transcript segment boundaries, enforces the requested 15–180 second duration range, filters malformed/low-quality results, and deduplicates overlapping candidates. Discovery jobs and candidate metadata are stored locally in SQLite.


## FFmpeg clip export (Phase 5)

Install a recent FFmpeg build and make sure `ffmpeg` is available on PATH. The Export Center creates an MP4/H.264 clip with AAC audio, fast-start metadata, square pixels, and a 1080p output frame for the selected 9:16, 16:9, or 1:1 ratio.

- NVIDIA NVENC is attempted when the FFmpeg build advertises `h264_nvenc`. If the encoder fails at runtime (for example, unavailable hardware or driver support), the renderer retries with CPU `libx264`.
- Output is written to a temporary `.partial.mp4` file and renamed only after FFmpeg succeeds and the file passes a basic size check. Failed partial output is removed.
- Export job metadata is stored in SQLite and completed files are saved under the local `exports` directory. The API exposes `GET /api/exports`, `GET /api/exports/{job_id}`, and `GET /api/exports/{job_id}/file`.
- Optional subtitle burn-in uses the edited transcript text and ASS subtitles with Clean, Bold, or Highlight styling. Enable it in Subtitle Studio; the selected preset and edited segments are persisted with the export job.
- Subtitle burn-in requires an FFmpeg build with the `subtitles` filter (libass). The API rejects burn-in requests with a clear error when that filter is unavailable; it never silently exports without captions. Temporary ASS files are removed after rendering.
- Export supports an ordered sequence of up to 100 trimmed ranges from one source video. Each range is rendered to a normalized MP4 segment, then concatenated in the selected order. Optional subtitle burn-in is applied to each segment using the original source timestamps.
- Crop controls include zoom (1.0×–3.0×) and normalized horizontal/vertical focal position. The selected framing is applied to both the preview monitor and every rendered segment, and is persisted with the export job.
- Split markers can be converted into individual sequence ranges with one action; the resulting segments are real render inputs, not just visual markers.
- The editor sequence is stored in the local draft. Add the current trim range, reorder clips, remove clips, then render the sequence as one MP4. A sequence can contain repeated or out-of-order ranges from the same source video.
- Multi-source assembly, visual multi-track timeline, true batch scheduling, live progress, cancellation, and automatic resumption after a crash are not implemented yet.
