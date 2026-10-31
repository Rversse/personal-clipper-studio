# Personal Clipper Studio

A local-first Windows desktop application for turning long-form video into short-form clips for YouTube Shorts and Facebook Reels.

## Project status

**Phase 1 — foundation.** The repository is being initialized. The first milestone establishes the desktop shell, frontend, local API service, and architecture notes. Media processing and AI workflows will be added in subsequent milestones.

## Product principles

- Local-first: project data and source media remain on the user's machine by default.
- Human-in-the-loop: AI proposes moments; the user reviews and edits before export.
- Non-destructive workflow: source media is never modified.
- Background processing: transcription and rendering must not block the interface.
- Transparent providers: Gemini is the default analysis provider; local Ollama support is optional.

## Planned architecture

- Desktop: Tauri 2
- UI: React, TypeScript, Vite
- Local service: Python, FastAPI
- Media pipeline: FFmpeg (planned)
- Transcription: local Whisper implementation (planned)
- AI analysis: Gemini API, with optional Ollama provider
- Local persistence: SQLite and application-managed media folders

## Development

The initial scaffold and setup instructions will be documented as the foundation is committed. Windows packaging and hardware-specific acceleration must be verified on a Windows machine.

## Legal note

Only import or process videos that you own or are authorized to use. The app does not grant permission to download or reuse third-party YouTube content.
