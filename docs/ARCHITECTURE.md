# Architecture

## Current foundation

```text
Tauri desktop shell
  └── React + TypeScript + Vite UI
        └── HTTP on loopback
              └── FastAPI local service
                    ├── Project and settings persistence (planned)
                    ├── Media inspection / FFmpeg pipeline (planned)
                    ├── Local Whisper transcription (planned)
                    └── AI provider adapters: Gemini / Ollama (planned)
```

## Design constraints

1. The desktop app is the user-facing process; the local API is a managed child service in packaged builds.
2. Bind the API to loopback, never to all network interfaces by default.
3. Keep source media untouched. Derived assets live in an app-managed workspace.
4. Persist project metadata locally in SQLite. Keep API credentials in the operating system credential store when integrated; never commit secrets.
5. Run transcription and rendering as background jobs with explicit state and cancellation.
6. AI output is advisory. Every candidate is reviewed by the user before export.
7. Keep media processing provider-independent so FFmpeg and transcription engines can be replaced.

## Planned processing pipeline

Import → probe metadata → extract audio / scene signals → transcribe → discover candidate moments → user review → edit / caption → render queue → export.

## Security and content

- Local API is loopback-only.
- API keys must be supplied by the user and stored outside source control.
- Users must own or have permission to process source video. No arbitrary third-party YouTube downloading is assumed.
