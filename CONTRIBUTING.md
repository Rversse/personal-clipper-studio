# Contributing

Thanks for taking the time to improve Personal Clipper Studio. The project is actively developed, so please keep contributions focused and describe behavior changes clearly.

## Before starting

1. Check the existing issues and pull requests for duplicate work.
2. For a substantial change, open an issue first to discuss scope and design.
3. Avoid bundling unrelated refactors with a bug fix or feature.
4. Never commit credentials, API keys, personal media, SQLite databases, local model files, or generated build output.

## Development environment

The primary target is Windows 10/11.

- Node.js 20+
- Python 3.11+
- Rust stable and Tauri v2 Windows prerequisites
- FFmpeg and FFprobe for media-related functionality

Follow the setup instructions in the root [README](README.md), [development guide](docs/DEVELOPMENT.md), and [Windows installation guide](docs/WINDOWS_INSTALLATION.md).

## Quality checks

Before opening a pull request, run the checks relevant to your change.

Frontend:

```powershell
npm install
npm run build
```

Backend:

```powershell
cd backend
python -m pip install -e ".[dev]"
pytest -q
```

For media pipeline changes, include or update tests that exercise the relevant FFmpeg behavior where practical. For Windows packaging or credential-store changes, describe the Windows-specific validation performed.

If a check cannot be run locally, state that clearly in the pull request. Do not report an unrun check as passing.

## Pull requests

A good pull request should:

- Explain the user-visible problem and the proposed solution.
- Describe important implementation choices and trade-offs.
- Include tests and their actual results.
- Include screenshots or a short recording for meaningful UI changes.
- Document configuration changes and migration considerations.
- Identify limitations or follow-up work.
- Keep secrets and private user data out of logs, screenshots, and test fixtures.

By submitting a contribution, you confirm that you have the right to submit the work under the repository's applicable terms. This repository currently has no license file; ask the maintainer before contributing code intended for redistribution.
