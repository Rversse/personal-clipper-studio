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
