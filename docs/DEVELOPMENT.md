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
backend\.venv\Scripts\Activate.ps1
pip install -e ./backend
uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765 --reload
```

The dashboard checks `GET /api/health` to show whether the local service is reachable.

## Desktop shell

```bash
npm run tauri dev
```

Build installers on Windows:

```bash
npm run tauri build
```

The current scaffold does not yet manage the FastAPI process from Tauri. During development, start the API separately. Process lifecycle integration is a follow-up foundation task before the first end-to-end media workflow.

## Notes

- Use a supported Windows environment to validate MSI/NSIS output.
- GPU acceleration is not assumed; later media pipeline setup will detect FFmpeg encoders and fall back safely.
- Do not place Gemini or other API keys in source files or commit local `.env` files.
