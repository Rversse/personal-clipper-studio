# Roadmap

## Phase 1 — foundation (complete)
- [x] Initialize repository and product architecture
- [x] React + TypeScript workspace shell
- [x] FastAPI local health service
- [x] Tauri v2 native shell and Windows build configuration
- [x] Local development and packaging instructions

## Phase 2 — projects and media
- [x] Project creation and local project database
- [x] Local video import and metadata probe (FFprobe integration; gracefully reports unavailable)
- [x] Thumbnail and playback preview (FFmpeg optional; browser preview available)
- [x] Durable media workspace

## Phase 3 — transcription and AI discovery (implemented; provider runtime setup required)
- [x] Local Whisper transcription with background job status and persisted segments
- [x] Auto Discovery and Category Analysis
- [x] Gemini Interactions API adapter and optional Ollama adapter
- [x] Candidate scoring, rationale, timestamp validation, and deduplication
- [x] Responsive AI Workspace with saved job restoration
- [ ] Live provider smoke test with a configured Gemini key or local Ollama model

## Phase 4 — editing and subtitles (partial)
- [x] Single-clip trim range, split markers, and local edit draft
- [x] 9:16, 16:9, and 1:1 export framing controls
- [x] Transcript text editing and visual subtitle presets (draft only)
- [ ] Multi-clip timeline, rendered split segments, crop repositioning, and burned-in subtitles
- [ ] Safe context-aware clip boundaries

## Phase 5 — rendering and delivery (partial)
- [x] Single-clip FFmpeg H.264 MP4 render pipeline with atomic output replacement
- [x] NVENC attempt with automatic libx264 CPU fallback on encoder failure
- [x] Persistent export job records, status endpoint, history, and completed-file download
- [ ] True multi-item batch scheduling, live progress, cancellation, and crash recovery
- [ ] Burned-in subtitle rendering and advanced Shorts / Reels export presets

## Phase 6 — hardening
- [ ] Error recovery and cancellation
- [ ] Windows installer and clean-machine verification
- [ ] Performance profiling on target hardware
