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
- [x] Transcript text editing and visual subtitle presets
- [x] Optional FFmpeg ASS subtitle burn-in with edited transcript text
- [x] Subtitle Studio controls for font size, vertical position, outline, and background opacity, persisted through export rendering
- [x] Ordered multi-clip sequence with per-clip trim ranges and reorder/remove controls
- [x] FFmpeg sequence assembly with optional subtitle burn-in
- [x] Global crop zoom and horizontal/vertical focal positioning in preview and FFmpeg output
- [x] Per-clip crop zoom and focal positioning, sequence selection, local draft persistence, and independent FFmpeg rendering
- [x] Convert split markers into renderable sequence clips
- [x] Source timeline ruler, live playhead, selected-range and split-point visualization, plus clickable sequence strip
- [x] Visual Video / linked Audio / transcript Captions timeline with shared output-time ruler
- [x] Timeline clip selection, drag reorder, edge trim, playhead seeking, split, and synchronized export sequence
- [x] Per-clip audio gain and mute controls persisted in the sequence and applied by FFmpeg
- [x] Direct caption move/trim on the timeline, precise IN/OUT controls, local draft persistence, and burn-in timestamp propagation
- [ ] Separate audio sources, audio fades, and full independent audio-track assembly
- [x] Context-aware AI candidate boundary refinement using punctuation, natural pauses, conjunction and sentence-continuation signals
- [x] Persist/display boundary quality score and opening/ending rationale on candidates
- [x] Word-level transcription timestamps, persistence, API exposure, and per-word Subtitle Studio timing corrections
- [x] Word-level timeline visualization with direct drag/trim editing, server persistence, and segment-level fallback
- [x] Word-level candidate boundary alignment with punctuation, pause, conjunction, and duration constraints; segment fallback when word timestamps are unavailable or invalid
- [x] Best-effort FFmpeg audio analysis for natural silence intervals and timestamped RMS energy envelope, integrated into candidate boundary scoring
- [ ] Pitch/F0, speech-rate, and richer prosodic emphasis analysis

## Phase 5 — rendering and delivery (partial)
- [x] Single-clip and ordered multi-clip FFmpeg H.264 MP4 render pipeline with atomic output replacement
- [x] NVENC attempt with automatic libx264 CPU fallback on encoder failure
- [x] Persistent export job records, status endpoint, history, and completed-file download
- [x] Live FFmpeg progress streamed into persistent export job status
- [x] Persistent single-worker queue for multiple export jobs, serial execution, queued/active cancellation, subprocess termination, and temporary-file cleanup
- [x] Automatic recovery of interrupted renders: restart incomplete jobs from persisted settings, preserve atomically completed MP4 outputs, and clean job-scoped temporary artifacts
- [x] Burned-in subtitle rendering with Clean, Bold, and Highlight presets
- [x] Platform export profiles for YouTube Shorts, Instagram Reels, Facebook Reels, Square Feed, Landscape, and Custom
- [x] Balanced / High / Maximum CPU and NVENC quality profiles with configurable source / 24 / 30 / 60 FPS
- [x] Optional FFmpeg loudnorm audio normalization with persisted per-job settings
- [ ] Two-pass loudness analysis and per-project export defaults

## Phase 6 — hardening
- [ ] Error recovery and cancellation
- [x] Windows NSIS/MSI CI packaging with a PyInstaller-bundled FastAPI service and packaged-backend health smoke test
- [x] Tauri-managed Windows backend startup/shutdown, loopback binding, port-collision guard, and persistent backend log
- [ ] Install and launch MSI/NSIS on a clean physical Windows machine; verify FFmpeg, CPU Whisper model download, and optional GPU/NVENC behavior
- [ ] Performance profiling on target hardware
