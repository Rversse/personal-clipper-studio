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

## In-app System Check

Open **Settings → System check** in the desktop app to inspect required and optional runtime components. The page checks whether both `ffmpeg.exe` and `ffprobe.exe` can be launched from the app's inherited `PATH`, whether the bundled faster-whisper runtime imports successfully, and whether the selected AI provider has its minimum configuration. Missing FFmpeg/FFprobe includes a link to a Windows build page and instructions to add its `bin` directory to `PATH`. Ollama is optional and has a separate official Windows download link; it is not required when using Gemini.

Python does not need a separate installation for the packaged Windows app. The CPU Whisper runtime is bundled; selected Whisper model weights download automatically on first use. If the bundled runtime itself is unavailable, repair or reinstall the application rather than installing Python packages into a separate system Python environment. After installing FFmpeg, restart the desktop app and run **Check again** so the backend receives the updated `PATH`.

The System Check is diagnostic only and does not install software. It does not test Gemini credentials with a network request. When Ollama is selected, it makes a short local HTTP check to the configured loopback address (`localhost`, `127.0.0.1`, or `::1`) and reads `/api/tags` to confirm whether the selected model is present. Non-loopback Ollama endpoints are deliberately not contacted. The page never displays API keys, endpoint URLs, or remote service details.

## First launch

- Packaged builds start the local API automatically at `127.0.0.1:8765`.
- If that port is already in use, the app refuses to start its own backend rather than connecting to an unrelated local process.
- The frontend retries while the backend starts and monitors it while the app is open.
- Backend logs are written to the application log directory in `backend.log`.
- The first transcription downloads the selected Whisper model. The model is not embedded in the installer and requires disk space and an internet connection. System Check and the AI Workspace inspect the Hugging Face model cache for the selected model and estimate its download size against free space on the cache volume. The estimate includes a 512 MB safety reserve; if free space can be measured and is below the estimate, starting transcription is blocked before a job is queued. Approximate model sizes vary by revision and cache state. If the operating system does not report free space, the app warns that capacity is unknown rather than blocking the job.

## AI providers and GPU

Gemini API keys can be entered from **Settings → Gemini API configuration**. On Windows, the app stores the key in the current user's Windows Credential Manager using the Windows Vault backend; the key is not written to the SQLite database, application settings files, or logs. The UI only receives whether a key is configured and its source, never the key value. Replacing or deleting a key is supported from the same panel.

The **Test connection** action sends a read-only HTTPS request to Google's Gemini model metadata endpoint using the key in the `x-goog-api-key` request header. It verifies that the selected model is accessible without sending a prompt or consuming generation tokens. The key is not placed in the URL. If `PCS_GEMINI_API_KEY` is set in the process environment, it takes precedence over the stored key and the UI marks it as environment-managed; update that environment variable to change it. The environment-variable path remains supported for development and managed deployments.

Windows Credential Manager is an at-rest protection for the current Windows user account, not a defense against malware or another process already running as that same user. Ollama must be installed and running locally when selected.

The bundled transcription engine is the CPU build. CUDA/cuDNN is not bundled. NVENC availability depends on the locally installed FFmpeg build, NVIDIA hardware, and compatible drivers; CPU H.264 remains the fallback when supported by FFmpeg.

## CI smoke-test scope

The Windows workflow builds the backend directory on Windows, runs an import check for the bundled faster-whisper, CTranslate2, PyAV, and tokenizers runtime, starts the executable on a temporary loopback port, verifies health, creates and lists a test project in SQLite, checks that Windows Credential Manager is available, performs a temporary secret save/read/delete round-trip without returning the secret to the API client, and checks clean process termination. It then builds both NSIS and MSI packages, silently installs each one, locates and launches the installed desktop application, verifies its managed local API and SQLite create/list workflow, closes the app cleanly, uninstalls the package, and checks that its registration and loopback listener are gone. These checks run on an ephemeral GitHub-hosted Windows runner; they do not replace a manual test on a separate physical Windows 11 machine with that machine's FFmpeg installation, media files, and hardware.
