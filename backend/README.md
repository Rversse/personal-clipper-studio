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

The discovery endpoint requires a completed transcription. It analyzes transcript chunks sequentially with overlap, validates returned timestamps against real transcript segment boundaries, enforces the requested 15–180 second duration range, filters malformed/low-quality results, and deduplicates overlapping candidates. Discovery jobs and candidate metadata are stored locally in SQLite. When FFmpeg is available, a best-effort audio pass detects natural silence intervals and a 0.5-second RMS energy envelope; these signals lightly adjust boundary quality without overriding word alignment or duration constraints. Missing audio, unsupported streams, FFmpeg errors, or timeouts fall back to transcript-only refinement.


## FFmpeg clip export (Phase 5)

Install a recent FFmpeg build and make sure `ffmpeg` is available on PATH. The Export Center creates an MP4/H.264 clip with AAC audio, fast-start metadata, square pixels, and a 1080p output frame for the selected 9:16, 16:9, or 1:1 ratio.

- NVIDIA NVENC is attempted when the FFmpeg build advertises `h264_nvenc`. If the encoder fails at runtime (for example, unavailable hardware or driver support), the renderer retries with CPU `libx264`.
- Output is written to a temporary `.partial.mp4` file and renamed only after FFmpeg succeeds and the file passes a basic size check. Failed partial output is removed.
- Export job metadata is stored in SQLite and completed files are saved under the local `exports` directory. FFmpeg progress is streamed while rendering runs and reflected in the Export Center without waiting for the process to finish. The API exposes `GET /api/exports`, `GET /api/exports/{job_id}`, `POST /api/exports/{job_id}/cancel`, and `GET /api/exports/{job_id}/file`.
- Optional subtitle burn-in uses edited transcript text and ASS subtitles with Clean, Bold, or Highlight styling. Subtitle Studio exposes font size (24–120), vertical position (5–30% from the bottom), outline width (0–8), and background opacity (0–90%). The style and edited segments are persisted with each export job.
- Subtitle burn-in requires an FFmpeg build with the `subtitles` filter (libass). The API rejects burn-in requests with a clear error when that filter is unavailable; it never silently exports without captions. Temporary ASS files are removed after rendering.
- Export supports an ordered sequence of up to 100 trimmed ranges from one source video. Each range is rendered to a normalized MP4 segment, then concatenated in the selected order. Optional subtitle burn-in is applied to each segment using the original source timestamps.
- Crop controls include zoom (1.0×–3.0×) and normalized horizontal/vertical focal position. Each sequence clip stores its own framing; select a clip in the sequence strip to edit its crop. New clips inherit the current framing, and legacy requests fall back to the export-level framing. Preview follows the selected clip and each rendered segment uses that clip's independent crop values.
- Split markers can be converted into individual sequence ranges with one action; the resulting segments are real render inputs, not just visual markers.
- The editor sequence is stored in the local draft. Add the current trim range, reorder clips, remove clips, then render the sequence as one MP4. A sequence can contain repeated or out-of-order ranges from the same source video.
- The Clip Editor includes a synchronized visual timeline with Video, Audio, and transcript Captions tracks. Video blocks can be reordered by drag-and-drop, trimmed with edge handles, split at the playhead, and selected by clicking the timeline; trim values and sequence order feed the export renderer. Audio remains linked to each source clip, but each clip now has independent gain (0–200%; 0% mutes it), persisted in the local draft and applied by FFmpeg. Caption blocks can be moved and trimmed directly on the timeline or adjusted with precise IN/OUT fields; edited text and source timestamps persist in the local draft and are sent to ASS burn-in. Replacing audio sources, fades, and independent audio-track clips are not implemented yet.
- Multi-source assembly is not implemented yet.
- Export profiles are stored with each queued job: YouTube Shorts, Instagram Reels, Facebook Reels (9:16), Square Feed (1:1), Landscape (16:9), or Custom. Resolution follows the selected aspect ratio (1080×1920, 1080×1080, or 1920×1080). Platform presets are checked against the requested ratio; the API's legacy default remains Custom.
- Encoding quality presets apply consistent CPU/NVENC settings across every segment: Balanced (libx264 CRF 20 / NVENC CQ 23), High (CRF 18 / CQ 20), and Maximum (CRF 15 / CQ 17). Output frame rate can follow the source or be fixed to 24, 30, or 60 FPS. Settings are persisted in SQLite as part of the export job.
- Optional EBU-style single-pass loudness normalization uses FFmpeg loudnorm at −16 LUFS integrated, −1.5 dBTP true peak, and 11 LU loudness range. Per-clip gain is applied before normalization. Audio normalization is optional and off by default.
- Context-aware discovery boundary refinement snaps AI-proposed hooks and endings to valid Whisper word boundaries when available, with sentence-segment fallback. It scores punctuation, conjunctions, pauses, and sentence continuation while preserving the requested duration. When FFmpeg is available, optional audio analysis adds natural-silence and RMS-energy signals as small boundary-score adjustments; missing audio or FFmpeg failure falls back to transcript-only refinement. Each candidate persists a 0–100 boundary-quality score, opening/ending rationale, and context note. Pitch/F0 and speech-rate analysis are not implemented yet.
- The export worker serializes queued jobs (one FFmpeg render at a time). Queued and active exports can be cancelled; cancellation terminates the active FFmpeg process and cleans partial output, intermediate segments, concat lists, and temporary subtitle files. Queued jobs remain persisted across restarts; an interrupted active render is marked failed on startup and is not automatically resumed.

## Packaged Windows desktop runtime

The Windows desktop workflow builds `desktop_runner.py` into a PyInstaller onedir distribution and includes the CPU `faster-whisper` runtime. Tauri starts `resources/pcs-api/pcs-api.exe` as a managed child process at `127.0.0.1:8765` in release builds. On normal application exit, Tauri sends a shutdown command over the child's stdin, allowing FastAPI's lifespan cleanup to run; a bounded hard-kill is used only if graceful shutdown times out. The onedir layout keeps the service as one directly managed process, avoiding the extra bootloader/worker process used by PyInstaller onefile mode.

The packaged-backend smoke test first import-checks the bundled `faster-whisper`, CTranslate2, PyAV, and `tokenizers` runtime (including native DLL loading), then starts the generated executable on a temporary loopback port, verifies the health response, creates and lists a SQLite project, and confirms child-process termination. The Windows workflow also silently installs each NSIS/MSI package on an ephemeral runner, launches the installed desktop app, verifies its managed backend and SQLite operations, then uninstalls the app. Debug builds still require a separately started API process.

FFmpeg and FFprobe are not bundled. Install them separately and make sure both executables are on PATH before using media probing, audio analysis, thumbnails, or rendering. CUDA/cuDNN and NVENC are machine-specific and are not part of the Windows CPU backend bundle.
