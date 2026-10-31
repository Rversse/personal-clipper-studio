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
