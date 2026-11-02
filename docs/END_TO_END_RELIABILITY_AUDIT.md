# Stage 7 — end-to-end reliability evidence

**Status: automated coverage substantially implemented; manual Windows/provider acceptance remains open.**

This document distinguishes automated regression evidence from checks that still require a configured provider and a physical Windows device. A green CI run is not evidence that a real Gemini/Ollama account, a user's media, or target hardware has been exercised.

## Automated coverage

| Area | Evidence | What it proves | Boundary |
|---|---|---|---|
| Connected API workflow | `backend/tests/test_end_to_end_pipeline.py::test_import_transcribe_discover_and_export_pipeline` | Imports media, persists word timestamps, edits a word timestamp, runs discovery, exports multiple clips, verifies subtitle payload, per-clip crop and audio gain/mute, export history, and download response. | Transcription, discovery provider, and renderer are deterministic test doubles in this test. |
| Real media and renderer | `backend/tests/test_end_to_end_pipeline.py::test_real_media_api_import_transcribe_discover_and_export` | Generates a local video/audio fixture, uses real FFmpeg/FFprobe for import metadata, thumbnail and multi-clip rendering, then verifies H.264/AAC, 1080×1920 dimensions, duration, and downloaded bytes. | Transcription and AI discovery are deterministic test doubles; this is not a real-provider or real-Whisper acceptance test. |
| Restart recovery | `backend/tests/test_worker_restart_recovery.py` | Recovers persisted transcription, discovery, and export jobs; checks requeue/resume, bounded crash retries, terminal user cancellation, shutdown races, and preservation/cleanup of export artifacts. | Exercises persisted application state and worker recovery in tests; it is not a physical desktop power-loss test. |
| Recovered export delivery | `backend/tests/test_worker_restart_recovery.py::test_recovered_export_history_and_download_survive_application_restart` | Verifies export history and completed-file download after reopening application storage. | Uses test fixtures rather than a user's actual project directory. |
| Render artifact validation | `backend/tests/test_rendering.py` | Covers FFprobe validation, invalid/mismatched outputs, atomic publish, interrupted-render recovery, real FFmpeg sequence rendering, cancellation, and per-clip render settings. | Platform codec availability and GPU behavior still vary by machine. |
| Windows packaging | `.github/workflows/windows-build.yml` and `docs/WINDOWS_INSTALLATION.md` | Hosted Windows CI builds NSIS/MSI, validates packaged backend/runtime, installs and launches both packages, smoke-tests API/SQLite, then uninstalls and checks process cleanup. | Hosted ephemeral runner is not a clean physical user machine; it does not validate the user's PATH, media, GPU drivers, or first-run model download. |

## Remaining manual acceptance

The following remain explicitly open:

1. Install an unsigned preview MSI or NSIS package on a separate clean Windows machine.
2. Confirm FFmpeg and FFprobe detection using that machine's actual PATH.
3. Configure a real Gemini API key and run the read-only model-access test, or configure a local Ollama instance with the selected model. Then run a real discovery request.
4. Download the selected faster-whisper model and transcribe real local media using the packaged CPU runtime.
5. Run import → transcription → discovery → editing → multi-clip export from the desktop UI and inspect the resulting MP4.
6. Close/relaunch the desktop app during active transcription, discovery, and export; verify queued work resumes, explicit user cancellation remains terminal, and completed exports remain downloadable.
7. Record hardware-specific FFmpeg/NVENC behavior. CPU fallback is covered by automated tests, but target GPU/driver combinations are not certified.
8. Uninstall the package and verify expected app behavior and data-retention policy on the physical machine.

## Exit criteria

Stage 7 must not be marked complete until the manual acceptance above is recorded with the Windows version, app build/commit, FFmpeg/FFprobe versions, provider mode (never the API key), Whisper model, GPU/driver details when applicable, and pass/fail notes. Do not attach secrets, personal media, or credential values to the audit record.

Automated test coverage can be rerun with the repository's normal backend test command. The real-media integration test skips when FFmpeg or FFprobe is unavailable; a skip must not be reported as a pass.
