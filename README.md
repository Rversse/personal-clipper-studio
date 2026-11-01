# Personal Clipper Studio

A local-first Windows desktop workspace for turning long-form videos into short-form clips. The application combines a Tauri v2 desktop shell, a React/TypeScript interface, and a local FastAPI media service.

> **Project status:** actively developed. This repository is not yet a stable release. Features, setup steps, and installer behavior may change.

## What it does

- Import and manage local video projects.
- Transcribe media locally with faster-whisper.
- Discover candidate clips from transcript context using Gemini or a local Ollama provider.
- Review and edit clips on a timeline, including trim ranges, sequence order, crop/framing, audio gain, and captions.
- Render MP4 clips with FFmpeg, including optional subtitle burn-in and platform-oriented aspect ratios.
- Keep project data, transcripts, drafts, and exports on the user's machine.

The app is designed as a local desktop tool. Its backend binds to loopback; it is not intended to be deployed as a public web service.

## Architecture

- **Desktop:** Tauri v2 and Rust
- **Frontend:** React 19, TypeScript, Vite
- **Local API:** Python 3.11+, FastAPI, Uvicorn
- **Storage:** SQLite and local filesystem
- **Media:** FFmpeg / FFprobe
- **Transcription:** faster-whisper (optional runtime dependency)
- **AI discovery:** Gemini API or local Ollama

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the current design and [docs/ROADMAP.md](docs/ROADMAP.md) for implementation status.

## Requirements

For development:

- Windows 10/11 is the primary desktop target.
- Node.js 20+
- Python 3.11+
- Rust stable toolchain and the Tauri v2 Windows prerequisites
- FFmpeg and FFprobe on PATH for media probing, thumbnails, audio analysis, and export
- WebView2 Runtime (normally present on supported Windows installations)

Optional:
- faster-whisper for local transcription
- A Gemini API key for cloud-based AI clip discovery, or Ollama with a compatible local model

## Development setup

### 1. Install frontend dependencies

From the repository root:

```powershell
npm install
npm run dev
```

### 2. Set up the local backend

In a second terminal, from the repository root:

```powershell
python -m venv backend/.venv
backend\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e "backend[dev,transcription]"
uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765 --reload
```

Backend health endpoint: http://127.0.0.1:8765/api/health

### 3. Run the desktop app

With the backend running, use another terminal:

```powershell
npm run tauri -- dev
```

Additional setup and Windows packaging details are in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and [docs/WINDOWS_INSTALLATION.md](docs/WINDOWS_INSTALLATION.md).

## Configuration and privacy

- User media, SQLite data, model caches, drafts, and exports are stored locally by default.
- The local API listens on `127.0.0.1`; do not expose it to a public network.
- Gemini API credentials should be configured through the app's secure settings or the backend environment. Never commit real credentials.
- AI discovery sends transcript text to the selected provider. It does not upload the original video as part of the discovery request. Review the selected provider's data-handling terms before using it.
- Ollama can run locally; model downloads and inference resource requirements depend on the model selected.
- FFmpeg and FFprobe are separate dependencies and are not bundled with the app.

See [backend/README.md](backend/README.md) for backend configuration details.

## Testing and builds

The repository includes GitHub Actions workflows for frontend/backend CI and Windows installer build smoke tests.

Run frontend checks:

```powershell
npm install
npm run build
```

Run backend tests:

```powershell
cd backend
python -m pip install -e ".[dev]"
pytest -q
```

Windows installer artifacts are produced by the Windows desktop workflow when its required runner is available. The workflow currently builds unsigned NSIS and MSI installers; users may see Windows SmartScreen warnings until a release is signed with a trusted code-signing certificate.

## Security

Please do not publish credentials, personal media, database files, logs containing private data, or generated installers with embedded secrets. Read [SECURITY.md](SECURITY.md) before reporting a vulnerability.

## Contributing

Bug reports and focused pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue or pull request.

## License

Unless a file or component states otherwise, the original source code in this repository is licensed under the **GNU General Public License v3.0 or later (GPL-3.0-or-later)**. See [LICENSE](LICENSE) for the complete license text.

Copyright (C) 2026 Rversse.

GPL-3.0-or-later permits use, modification, and redistribution, including commercial redistribution. If you distribute a modified or combined covered version, you must comply with the GPL's applicable source-code and notice requirements. Private modification and internal use do not, by themselves, require publication of your changes.

Third-party libraries, tools, icons, and other assets remain under their own respective licenses; this project license does not replace those terms. Review the relevant upstream notices before redistributing a build.

This project is provided without warranty, to the extent permitted by applicable law.
