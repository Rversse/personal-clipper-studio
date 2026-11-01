# Development setup

## Prerequisites

- Node.js 20 or newer
- npm
- Python 3.11 or newer
- Rust stable toolchain
- Tauri v2 platform prerequisites for Windows (Microsoft C++ Build Tools and WebView2 Runtime)

## Frontend

```bash
npm install
npm run dev
```

Vite listens on `http://127.0.0.1:1420`.

## Local API

In a second terminal:

```powershell
python -m venv backend/.venv
backend\\.venv\\Scripts\\Activate.ps1
pip install -e "./backend[transcription]"
uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765 --reload
```

The dashboard polls `GET /api/health` until the service is available and checks it periodically while the app is open. The API must remain bound to loopback.

## Desktop development shell

```bash
npm run tauri dev
```

Debug builds intentionally do not start the backend automatically. Run the local API in a second terminal using the command above.

## Windows installers

The Windows desktop workflow builds both unsigned NSIS (`.exe`) and MSI installers. It packages the FastAPI service as a Windows executable with the CPU `faster-whisper` runtime, runs that executable against `/api/health`, then builds the Tauri installers.

1. Open the repository's **Actions → Windows desktop build** workflow.
2. Run it with **workflow_dispatch**, or push a change to a pull request targeting `main`.
3. Download the `personal-clipper-studio-windows` workflow artifact.

The packaged release starts the API at `127.0.0.1:8765` and requests graceful FastAPI shutdown when the desktop app exits; a hard-kill is only used after a bounded timeout. Backend logs are written to the application log directory as `backend.log`.

**FFmpeg and FFprobe must still be installed separately and available on PATH.** They are required for media probing, thumbnails, audio analysis, and video rendering. See [Windows installation notes](WINDOWS_INSTALLATION.md). The bundled Whisper engine runs on CPU; CUDA acceleration is not bundled or guaranteed.

## Notes

- Workflow installers are unsigned preview artifacts, not production-signed releases.
- WebView2 Runtime may be installed by the Tauri installer on first use; an internet connection may be required.
- The first transcription downloads the selected Whisper model. Model size affects download time, storage, memory, and speed.
- Gemini keys and other credentials must stay outside source control. The backend reads its provider settings from the process environment; never put secrets in frontend variables.
