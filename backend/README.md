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
