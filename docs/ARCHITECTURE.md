# Architecture

## Runtime architecture

```text
Tauri desktop process
  ├── React + TypeScript + Vite UI
  │     └── HTTP on 127.0.0.1:8765
  └── Managed FastAPI child process (packaged Windows build)
        ├── Project and media metadata (SQLite)
        ├── Media inspection / FFmpeg pipeline
        ├── CPU faster-whisper transcription
        ├── Word-level transcript and editing APIs
        └── AI provider adapters: Gemini / Ollama
```

The Windows release workflow builds the FastAPI entry point into a self-contained onedir distribution using PyInstaller. Tauri resolves that executable from bundled resources, starts it bound to loopback, captures its output in `backend.log`, and requests a graceful shutdown through the child's stdin when the desktop app exits. The host waits up to 12 seconds for FastAPI's lifespan cleanup before using a hard-kill fallback. Debug builds continue to use a manually started API process so reload and development tooling remain straightforward.

The frontend retries the health endpoint while the service is starting and continues checking for an unexpected service exit. It reloads the project list when the service reconnects.

## Design constraints

1. The desktop app is the user-facing process; packaged Windows builds manage the local API child process.
2. Bind the API to loopback only, never to all network interfaces by default.
3. Refuse to start the packaged service when port `8765` is already occupied rather than silently connecting to an unrelated process.
4. Keep source media untouched. Derived assets live in an app-managed workspace.
5. Persist project metadata locally in SQLite. Keep API credentials outside source control; operating-system credential-store integration remains future work.
6. Run transcription and rendering as background jobs with explicit state and cancellation.
7. On restart, requeue interrupted exports from persisted render settings; retain a final MP4 only when the renderer's atomic commit left a valid MP4 signature, and clean job-scoped temporary artifacts before retrying.
7. AI output is advisory. Every candidate is reviewed by the user before export.
8. Keep media processing provider-independent so FFmpeg and transcription engines can be replaced.

## Processing pipeline

Import → probe metadata → transcribe → discover candidate moments → user review → edit / caption → render queue → export.

## Packaging boundaries

- The Windows bundle includes the Python API runtime and CPU faster-whisper dependencies.
- FFmpeg and FFprobe remain separately installed system tools and must be available on PATH.
- NVIDIA CUDA/cuDNN and NVENC capabilities depend on the target machine and its FFmpeg/driver configuration.
- Gemini and Ollama providers are configured by the user; no API secrets or model weights are embedded in the installer.

## Security and content

- The API binds to `127.0.0.1` only.
- Packaged startup checks that the API port is free before launching the child.
- API keys must be supplied by the user and stored outside source control.
- Users must own or have permission to process source video. No arbitrary third-party YouTube downloading is assumed.
