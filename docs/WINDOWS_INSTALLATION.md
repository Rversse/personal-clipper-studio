# Windows installation notes

## What the preview installer contains

The Windows CI artifact contains the Tauri desktop application and a self-contained FastAPI service directory. The bundled service includes the Python runtime and CPU `faster-whisper` dependencies; Python does not need to be installed separately.

The installer is an unsigned preview build. Windows SmartScreen may show an unknown-publisher warning. Do not treat this CI artifact as a production-signed release.

## Required external dependency: FFmpeg

FFmpeg and FFprobe are not included in the installer. Install a Windows FFmpeg build that includes both tools, then add its `bin` directory to the system or user `PATH`.

Verify both commands in a new PowerShell window:

```powershell
ffmpeg -version
ffprobe -version
```

These tools are required for media metadata probing, thumbnail generation, optional audio boundary analysis, and clip rendering. If either command is unavailable, some media workflows will fail or return incomplete metadata.

Official Windows build information: https://www.gyan.dev/ffmpeg/builds/

## First launch

- Packaged builds start the local API automatically at `127.0.0.1:8765`.
- If that port is already in use, the app refuses to start its own backend rather than connecting to an unrelated local process.
- The frontend retries while the backend starts and monitors it while the app is open.
- Backend logs are written to the application log directory in `backend.log`.
- The first transcription downloads the selected Whisper model. The model is not embedded in the installer and requires disk space and an internet connection.

## AI providers and GPU

Gemini requires a user-supplied `PCS_GEMINI_API_KEY` in the environment inherited by the desktop app. Ollama must be installed and running locally when selected.

The bundled transcription engine is the CPU build. CUDA/cuDNN is not bundled. NVENC availability depends on the locally installed FFmpeg build, NVIDIA hardware, and compatible drivers; CPU H.264 remains the fallback when supported by FFmpeg.

## CI smoke-test scope

The Windows workflow builds the backend directory on Windows, runs an import check for the bundled faster-whisper, CTranslate2, PyAV, and tokenizers runtime, starts the executable on a temporary loopback port, verifies health, creates and lists a test project in SQLite, and checks clean process termination. It then builds both NSIS and MSI packages, silently installs each one, locates and launches the installed desktop application, verifies its managed local API and SQLite create/list workflow, closes the app cleanly, uninstalls the package, and checks that its registration and loopback listener are gone. These checks run on an ephemeral GitHub-hosted Windows runner; they do not replace a manual test on a separate physical Windows 11 machine with that machine's FFmpeg installation, media files, and hardware.
